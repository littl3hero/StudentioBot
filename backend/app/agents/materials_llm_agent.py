from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.agents import materials_agent

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


def run_materials_agent(
    student_id: str,
    profile: Dict[str, Any],
    focus_topics: Optional[List[str]] = None,
    weaknesses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    MaterialsAgent: отдельный LangChain-агент, который решает,
    какие материалы нужны, и вызывает генерацию/чтение материалов.
    """
    topics = focus_topics or _coerce_list(profile.get("topics"))
    weak = weaknesses or _coerce_list(profile.get("weaknesses"))

    # Фолбэк, если нет LLM/LangChain
    if not settings.OPENAI_API_KEY or ChatOpenAI is None or initialize_agent is None or AgentType is None or Tool is None:
        try:
            mats = materials_agent.generate_and_save_materials(student_id=student_id)
            m_count = len(mats or [])
            return {
                "status": "ok",
                "materials_prepared": m_count,
                "focus_topics": topics,
                "weaknesses": weak,
                "comment": "Материалы подготовлены в режиме фолбэка без полноценного MaterialsAgent.",
            }
        except Exception as e:
            return {
                "status": "error",
                "materials_prepared": 0,
                "focus_topics": topics,
                "weaknesses": weak,
                "comment": f"Не удалось подготовить материалы: {e}",
            }

    try:
        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )

        tools: List[Tool] = []

        # tool: получить краткий список уже существующих материалов
        def _tool_get_materials_summary(limit: int = 5) -> str:
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
                return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

        tools.append(
            Tool.from_function(
                func=_tool_get_materials_summary,
                name="get_materials_summary",
                description="Получить краткий список уже существующих материалов студента.",
            )
        )

        # tool: сгенерировать/обновить материалы
        def _tool_generate_materials() -> str:
            try:
                mats = materials_agent.generate_and_save_materials(student_id=student_id)
                m_count = len(mats or [])
                return json.dumps(
                    {
                        "status": "ok",
                        "materials_prepared": m_count,
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

        tools.append(
            Tool.from_function(
                func=_tool_generate_materials,
                name="generate_materials_for_student",
                description=(
                    "Сгенерировать и сохранить учебные материалы (конспекты, шпаргалки, ссылки) "
                    "для текущего студента."
                ),
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

        ctx = {
            "student_id": student_id,
            "profile": profile,
            "focus_topics": topics,
            "weaknesses": weak,
        }

        instructions = (
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
            "}\n\n"
            f"Данные студента:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        raw = agent.run(instructions)
        if not isinstance(raw, str):
            raw = str(raw)

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("MaterialsAgent: no-json-in-output")

        payload = raw[start : end + 1]
        data = json.loads(payload)

        status = str(data.get("status") or "ok")
        mp = int(data.get("materials_prepared") or 0)
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
        print(f"[MaterialsAgent] failed: {e}")
        try:
            mats = materials_agent.generate_and_save_materials(student_id=student_id)
            m_count = len(mats or [])
            return {
                "status": "ok",
                "materials_prepared": m_count,
                "focus_topics": topics,
                "weaknesses": weak,
                "comment": "MaterialsAgent не сработал, материалы подготовлены напрямую.",
            }
        except Exception as e2:
            return {
                "status": "error",
                "materials_prepared": 0,
                "focus_topics": topics,
                "weaknesses": weak,
                "comment": f"Не удалось подготовить материалы: {e2}",
            }
