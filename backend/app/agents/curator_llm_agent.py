from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.memory.vector_store_pg import get_last_curator_snapshot, fetch_recent_memory

# Пытаемся импортировать новый LangChain-стек
try:
    from langchain_openai import ChatOpenAI  # type: ignore
    from langchain.tools import tool as lc_tool  # type: ignore
    from langchain.agents import create_agent  # type: ignore

    _LC_IMPORT_ERROR: Optional[Exception] = None
    print("[CuratorAgent] LangChain imports OK")
except Exception as e:
    ChatOpenAI = None  # type: ignore
    lc_tool = None  # type: ignore
    create_agent = None  # type: ignore
    _LC_IMPORT_ERROR = e
    print(f"[CuratorAgent] LangChain import error: {repr(e)}")


def _coerce_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value)]


def _fallback_curator(student_id: str, profile: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """
    Фолбэк-режим без LangChain:
    просто используем базовый профиль.
    """
    print(f"[CuratorAgent] using fallback curator, reason={reason}")
    topics = _coerce_list(profile.get("topics"))
    weaknesses = _coerce_list(profile.get("weaknesses"))
    return {
        "summary": (
            "Дополнительный анализ недоступен, использую базовый профиль. "
            f"Темы: {', '.join(topics) if topics else '—'}. "
            f"Слабые места: {', '.join(weaknesses) if weaknesses else 'не явные'}."
        ),
        "recommended_topics": topics,
        "notes": "",
    }


def run_curator_agent(
    student_id: str,
    profile: Dict[str, Any],
    task: str = "",
) -> Dict[str, Any]:
    """
    CuratorAgent: отдельный агент-Куратор.
    Анализирует профиль и память студента и возвращает:
    - summary: краткое описание,
    - recommended_topics: приоритетные темы,
    - notes: доп. замечания.
    """
    # Фолбэк, если нет LLM/LangChain
    if (
        not settings.OPENAI_API_KEY
        or ChatOpenAI is None
        or lc_tool is None
        or create_agent is None
    ):
        return _fallback_curator(
            student_id=student_id,
            profile=profile,
            reason=f"no-llm-or-langchain (import_error={_LC_IMPORT_ERROR})",
        )

    try:
        try:
            snap = get_last_curator_snapshot(student_id)
        except Exception as e:
            print(f"[CuratorAgent] get_last_curator_snapshot failed: {e}")
            snap = None

        try:
            recent = fetch_recent_memory(
                student_id=student_id,
                kind=None,
                limit=5,
            )
        except Exception as e:
            print(f"[CuratorAgent] fetch_recent_memory failed: {e}")
            recent = []

        ctx = {
            "student_id": student_id,
            "profile": profile,
            "last_curator_snapshot": snap,
            "recent_memory": recent,
        }

        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )

        # tool: получить свежий snapshot при необходимости
        @lc_tool
        def get_student_snapshot() -> str:
            """
            get_student_snapshot:
            Получить последний сохранённый срез профиля студента от Куратора.
            Возвращает JSON {status, snapshot}.
            """
            try:
                snap2 = get_last_curator_snapshot(student_id)
                return json.dumps(
                    {"status": "ok", "snapshot": snap2}, ensure_ascii=False
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)}, ensure_ascii=False
                )

        # tool: добрать недавнюю память
        @lc_tool
        def get_recent_memory_tool(limit: int = 5) -> str:
            """
            get_recent_memory:
            Получить несколько последних записей из памяти студента.
            Возвращает JSON {status, records}.
            """
            try:
                recs = fetch_recent_memory(
                    student_id=student_id,
                    kind=None,
                    limit=max(1, min(20, int(limit))),
                )
                return json.dumps(
                    {"status": "ok", "records": recs}, ensure_ascii=False
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)}, ensure_ascii=False
                )

        tools = [get_student_snapshot, get_recent_memory_tool]

        system_prompt = (
            "Ты — CuratorAgent, специализированный агент-Куратор.\n"
            "У тебя есть профиль студента и доступ к инструментам, которые позволяют посмотреть "
            "последний срез и недавнюю память. На основе этого ты должен:\n"
            "1) Кратко описать текущий уровень, сильные и слабые стороны.\n"
            "2) Предложить 1–3 приоритетные темы для работы.\n\n"
            "Ответь строго в формате JSON без пояснений вокруг:\n"
            "{\n"
            '  \"summary\": \"краткое описание сильных и слабых сторон\",\n'
            '  \"recommended_topics\": [\"тема1\", \"тема2\"],\n'
            '  \"notes\": \"любые дополнительные замечания\"\n'
            "}\n"
        )

        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        instructions = (
            "Сделай анализ профиля и памяти студента и верни только JSON в указанном формате.\n\n"
            "Базовые данные профиля:\n"
            f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n"
            "Задача от оркестратора:\n"
            f"{task}\n"
        )

        print("[CuratorAgent] calling agent.invoke()...")
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": instructions,
                    }
                ]
            }
        )

        # ----- вынимаем текст из результата -----
        if isinstance(result, dict) and "messages" in result:
            msgs = result["messages"] or []
            last = msgs[-1] if msgs else None
            if last is not None:
                content = getattr(last, "content", None)
            else:
                content = None
        else:
            content = None

        if isinstance(content, str):
            raw_output = content
        elif isinstance(content, list):
            parts: List[str] = []
            for ch in content:
                if isinstance(ch, dict) and "text" in ch:
                    parts.append(str(ch["text"]))
                else:
                    parts.append(str(ch))
            raw_output = "\n".join(parts)
        else:
            raw_output = str(result)

        print(
            "[CuratorAgent] RAW AGENT OUTPUT (first 300 chars): "
            f"{repr(raw_output)[:300]}"
        )

        # ----- чистим ```json ... ``` -----
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"CuratorAgent: no-json-in-output: {cleaned[:200]}")

        payload = cleaned[start : end + 1]
        data = json.loads(payload)

        summary = str(data.get("summary") or "").strip()
        rec_topics = data.get("recommended_topics") or []
        notes = str(data.get("notes") or "")

        if not isinstance(rec_topics, list):
            rec_topics = _coerce_list(rec_topics)

        return {
            "summary": summary
            or "Анализ выполнен, используй рекомендованные темы для дальнейшей работы.",
            "recommended_topics": [
                str(t).strip() for t in rec_topics if str(t).strip()
            ],
            "notes": notes,
        }
    except Exception as e:
        print(f"[CuratorAgent] ERROR in LC-agent: {e}")
        return _fallback_curator(
            student_id=student_id,
            profile=profile,
            reason=f"lc-agent-error: {e}",
        )
