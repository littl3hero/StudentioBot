from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.memory.vector_store_pg import get_last_curator_snapshot, fetch_recent_memory

try:
    from langchain_openai import ChatOpenAI  # type: ignore
    from langchain.tools import Tool  # type: ignore
    from langchain.agents import initialize_agent, AgentType  # type: ignore
except Exception as e:
    ChatOpenAI = None  # type: ignore
    Tool = None  # type: ignore
    initialize_agent = None  # type: ignore
    AgentType = None  # type: ignore
    _LC_IMPORT_ERROR = e
else:
    _LC_IMPORT_ERROR = None


def _coerce_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value)]


def run_curator_agent(student_id: str, profile: Dict[str, Any], task: str = "") -> Dict[str, Any]:
    """
    CuratorAgent: отдельный LangChain-агент, который анализирует профиль и память студента
    и возвращает резюме + приоритетные темы.
    """
    # Фолбэк, если нет LLM/LangChain
    if not settings.OPENAI_API_KEY or ChatOpenAI is None or initialize_agent is None or AgentType is None or Tool is None:
        topics = _coerce_list(profile.get("topics"))
        weaknesses = _coerce_list(profile.get("weaknesses"))
        return {
            "summary": (
                "Дополнительный анализ недоступен (нет LLM), использую базовый профиль. "
                f"Темы: {', '.join(topics) if topics else '—'}. "
                f"Слабые места: {', '.join(weaknesses) if weaknesses else 'не явные'}."
            ),
            "recommended_topics": topics,
            "notes": "",
        }

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

        tools: List[Tool] = []

        # tool: получить свежий snapshot при необходимости
        def _tool_get_snapshot() -> str:
            try:
                snap2 = get_last_curator_snapshot(student_id)
                return json.dumps({"status": "ok", "snapshot": snap2}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

        tools.append(
            Tool.from_function(
                func=_tool_get_snapshot,
                name="get_student_snapshot",
                description="Получить последний сохранённый срез профиля студента от Куратора.",
            )
        )

        # tool: добрать недавнюю память
        def _tool_get_recent_memory(limit: int = 5) -> str:
            try:
                recs = fetch_recent_memory(
                    student_id=student_id,
                    kind=None,
                    limit=max(1, min(20, int(limit))),
                )
                return json.dumps({"status": "ok", "records": recs}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

        tools.append(
            Tool.from_function(
                func=_tool_get_recent_memory,
                name="get_recent_memory",
                description="Получить несколько последних записей из памяти студента.",
            )
        )

        agent = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=False,
            max_iterations=3,
            handle_parsing_errors=True,
        )

        instructions = (
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
            "}\n\n"
            f"Базовые данные профиля:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n"
            "Задача от оркестратора:\n"
            f"{task}\n"
        )

        raw = agent.run(instructions)
        if not isinstance(raw, str):
            raw = str(raw)

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("CuratorAgent: no-json-in-output")

        payload = raw[start : end + 1]
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
        print(f"[CuratorAgent] failed: {e}")
        topics = _coerce_list(profile.get("topics"))
        weaknesses = _coerce_list(profile.get("weaknesses"))
        return {
            "summary": (
                "CuratorAgent не смог выполнить анализ, использую базовый профиль. "
                f"Темы: {', '.join(topics) if topics else '—'}. "
                f"Слабые места: {', '.join(weaknesses) if weaknesses else 'не явные'}."
            ),
            "recommended_topics": topics,
            "notes": "",
        }
