from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.agents import materials_agent

# Пытаемся импортировать новый LangChain-стек
try:
    from langchain_openai import ChatOpenAI  # type: ignore
    from langchain.tools import tool as lc_tool  # type: ignore
    from langchain.agents import create_agent  # type: ignore

    _LC_IMPORT_ERROR: Optional[Exception] = None
    print("[MaterialsAgent] LangChain imports OK")
except Exception as e:
    ChatOpenAI = None  # type: ignore
    lc_tool = None  # type: ignore
    create_agent = None  # type: ignore
    _LC_IMPORT_ERROR = e
    print(f"[MaterialsAgent] LangChain import error: {repr(e)}")


def _coerce_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value)]


def _fallback_materials(
    student_id: str,
    topics: List[str],
    weaknesses: List[str],
    reason: str,
) -> Dict[str, Any]:
    """
    Фолбэк-режим без LangChain:
    просто генерируем/обновляем материалы и возвращаем сводку.
    """
    print(f"[MaterialsAgent] using fallback materials generation, reason={reason}")
    try:
        mats = materials_agent.generate_and_save_materials(student_id=student_id)
        m_count = len(mats or [])
        return {
            "status": "ok",
            "materials_prepared": m_count,
            "focus_topics": topics,
            "weaknesses": weaknesses,
            "comment": "Материалы подготовлены в режиме фолбэка без полноценного MaterialsAgent.",
        }
    except Exception as e:
        print(f"[MaterialsAgent] fallback generate_and_save_materials failed: {e}")
        return {
            "status": "error",
            "materials_prepared": 0,
            "focus_topics": topics,
            "weaknesses": weaknesses,
            "comment": f"Не удалось подготовить материалы: {e}",
        }


def run_materials_agent(
    student_id: str,
    profile: Dict[str, Any],
    focus_topics: Optional[List[str]] = None,
    weaknesses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    MaterialsAgent: отдельный агент, который решает,
    какие материалы нужны, и вызывает генерацию/чтение материалов.

    Режимы:
    - Если LangChain/LLM недоступны → простой фолбэк: generate_and_save_materials.
    - Если доступны → агент с двумя инструментами:
        * get_materials_summary — посмотреть, что уже есть;
        * generate_materials_for_student — сгенерировать/обновить материалы.
    """
    topics = focus_topics or _coerce_list(profile.get("topics"))
    weak = weaknesses or _coerce_list(profile.get("weaknesses"))

    # --- ФОЛБЭК, если нет ключа или нет LangChain ---
    if (
        not settings.OPENAI_API_KEY
        or ChatOpenAI is None
        or lc_tool is None
        or create_agent is None
    ):
        return _fallback_materials(
            student_id=student_id,
            topics=topics,
            weaknesses=weak,
            reason=f"no-llm-or-langchain (import_error={_LC_IMPORT_ERROR})",
        )

    # --- Основной путь: LangChain-агент с tools ---

    try:
        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )

        # tool: получить краткий список уже существующих материалов
        @lc_tool
        def get_materials_summary(limit: int = 5) -> str:
            """
            get_materials_summary:
            Вернуть краткий список уже существующих материалов студента.
            Возвращает JSON {status, materials: [{id, title, type}]}.
            """
            try:
                mats = materials_agent.get_materials_for_student(student_id=student_id)
                items = mats[: max(1, min(20, int(limit)))]
                simplified = [
                    {
                        "id": m.get("id"),
                        "title": m.get("title"),
                        "type": m.get("type"),
                    }
                    for m in items
                ]
                return json.dumps(
                    {"status": "ok", "materials": simplified}, ensure_ascii=False
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)}, ensure_ascii=False
                )

        # tool: сгенерировать/обновить материалы
        @lc_tool
        def generate_materials_for_student() -> str:
            """
            generate_materials_for_student:
            Сгенерировать и сохранить учебные материалы (конспекты, шпаргалки, ссылки)
            для текущего студента.
            Возвращает JSON {status, materials_prepared}.
            """
            try:
                mats = materials_agent.generate_and_save_materials(
                    student_id=student_id
                )
                m_count = len(mats or [])
                return json.dumps(
                    {
                        "status": "ok",
                        "materials_prepared": m_count,
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)}, ensure_ascii=False
                )

        tools = [get_materials_summary, generate_materials_for_student]

        ctx = {
            "student_id": student_id,
            "profile": profile,
            "focus_topics": topics,
            "weaknesses": weak,
        }

        system_prompt = (
            "Ты — MaterialsAgent, специализированный агент по учебным материалам.\n"
            "У тебя есть профиль студента и инструменты:\n"
            "- get_materials_summary: посмотреть, какие материалы уже есть;\n"
            "- generate_materials_for_student: сгенерировать/обновить материалы.\n\n"
            "Твоя задача:\n"
            "- Понять, какие темы сейчас наиболее приоритетны (по focus_topics и weaknesses).\n"
            "- При необходимости вызвать generate_materials_for_student (как минимум один раз),\n"
            "  чтобы у студента были актуальные материалы.\n\n"
            "Финальный ответ верни строго в формате JSON без пояснений вокруг:\n"
            "{\n"
            '  \"status\": \"ok\" | \"error\",\n'
            '  \"materials_prepared\": 3,\n'
            '  \"focus_topics\": [\"тема1\", \"тема2\"],\n'
            '  \"weaknesses\": [\"слабое место1\"],\n'
            '  \"comment\": \"краткое пояснение, какие материалы и зачем подготовлены\"\n'
            "}\n"
        )

        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        instructions = (
            "Определи, какие материалы нужны студенту, при необходимости обнови их "
            "и верни только JSON в указанном формате.\n\n"
            "Данные студента:\n"
            f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        print("[MaterialsAgent] calling agent.invoke()...")
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
            "[MaterialsAgent] RAW AGENT OUTPUT (first 300 chars): "
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
            raise ValueError(f"MaterialsAgent: no-json-in-output: {cleaned[:200]}")

        payload = cleaned[start : end + 1]
        data = json.loads(payload)

        status = str(data.get("status") or "ok")
        try:
            mp = int(data.get("materials_prepared") or 0)
        except Exception:
            mp = 0
        f_topics = data.get("focus_topics") or topics
        weak2 = data.get("weaknesses") or weak
        comment = str(data.get("comment") or "").strip()

        if not isinstance(f_topics, list):
            f_topics = _coerce_list(f_topics)
        if not isinstance(weak2, list):
            weak2 = _coerce_list(weak2)

        return {
            "status": status,
            "materials_prepared": mp,
            "focus_topics": f_topics,
            "weaknesses": weak2,
            "comment": comment
            or "Материалы обновлены. Открой раздел «Материалы», чтобы их посмотреть.",
        }
    except Exception as e:
        print(f"[MaterialsAgent] ERROR in LC-agent: {e}")
        return _fallback_materials(
            student_id=student_id,
            topics=topics,
            weaknesses=weak,
            reason=f"lc-agent-error: {e}",
        )
