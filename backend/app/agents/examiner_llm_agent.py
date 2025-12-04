from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.agents import examiner

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


def run_examiner_agent(
    student_id: str,
    profile: Dict[str, Any],
    count: Optional[int] = None,
    topic_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ExaminerAgent: отдельный LangChain-агент, который решает, какой экзамен подготовить,
    вызывает базовый генератор экзамена и возвращает сводку.
    """
    # защита от странных значений
    try:
        safe_count = max(1, min(20, int(count))) if count is not None else 5
    except Exception:
        safe_count = 5

    topics = _coerce_list(profile.get("topics"))
    default_topic_hint = topic_hint or (topics[0] if topics else None)

    # Фолбэк, если нет LLM/LangChain
    if not settings.OPENAI_API_KEY or ChatOpenAI is None or initialize_agent is None or AgentType is None or Tool is None:
        try:
            data = examiner.generate_exam(count=safe_count, student_id=student_id)
            try:
                examiner.set_prepared_exam(student_id, data)  # type: ignore[attr-defined]
            except Exception as e:
                print(f"[ExaminerAgent] fallback set_prepared_exam failed: {e}")
            questions = data.get("questions") or []
            return {
                "status": "ok",
                "questions_prepared": len(questions),
                "topic_hint": default_topic_hint,
                "comment": "Экзамен подготовлен в режиме фолбэка без полноценного ExamineAgent.",
            }
        except Exception as e:
            return {
                "status": "error",
                "questions_prepared": 0,
                "topic_hint": default_topic_hint,
                "comment": f"Не удалось подготовить экзамен: {e}",
            }

    try:
        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )

        tools: List[Tool] = []

        # tool: базовая генерация экзамена
        def _tool_generate_exam(count: int = safe_count, topic_hint: Optional[str] = default_topic_hint) -> str:
            try:
                safe_c = max(1, min(20, int(count)))
            except Exception:
                safe_c = safe_count
            try:
                data = examiner.generate_exam(count=safe_c, student_id=student_id)
                try:
                    examiner.set_prepared_exam(student_id, data)  # type: ignore[attr-defined]
                except Exception as e:
                    print(f"[ExaminerAgent] set_prepared_exam failed: {e}")
                questions = data.get("questions") or []
                summary = {
                    "status": "ok",
                    "questions_prepared": len(questions),
                    "topic_hint": topic_hint,
                }
                return json.dumps(summary, ensure_ascii=False)
            except Exception as e:
                err = {"status": "error", "error": str(e)}
                return json.dumps(err, ensure_ascii=False)

        tools.append(
            Tool.from_function(
                func=_tool_generate_exam,
                name="generate_exam_for_student",
                description=(
                    "Сгенерировать и сохранить тренировочный тест для текущего студента. "
                    "Аргументы: count (1–20) и topic_hint (строка с темой/подтемой)."
                ),
            )
        )

        agent = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=False,
            max_iterations=2,
            handle_parsing_errors=True,
        )

        ctx = {
            "student_id": student_id,
            "profile": profile,
            "requested_count": safe_count,
            "requested_topic_hint": default_topic_hint,
        }

        instructions = (
            "Ты — ExaminerAgent, специализированный агент-Экзаменатор.\n"
            "У тебя есть профиль студента и tool generate_exam_for_student, который реально создаёт и сохраняет тест.\n"
            "Твоя задача — подготовить разумный тренировочный экзамен по нужной теме/темам.\n\n"
            "Требования:\n"
            "- Вызови generate_exam_for_student РОВНО ОДИН раз.\n"
            "- Выбери осмысленное число вопросов (обычно 3–10) и тему/подтему.\n\n"
            "Финальный ответ верни строго в формате JSON без пояснений вокруг:\n"
            "{\n"
            '  \"status\": \"ok\" | \"error\",\n'
            '  \"questions_prepared\": 5,\n'
            '  \"topic_hint\": \"строка с темой\",\n'
            '  \"comment\": \"краткое пояснение, какой тест подготовлен\"\n'
            "}\n\n"
            f"Данные студента:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        raw = agent.run(instructions)
        if not isinstance(raw, str):
            raw = str(raw)

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("ExaminerAgent: no-json-in-output")

        payload = raw[start : end + 1]
        data = json.loads(payload)

        status = str(data.get("status") or "ok")
        qp = int(data.get("questions_prepared") or 0)
        th = data.get("topic_hint") or default_topic_hint
        comment = str(data.get("comment") or "").strip()

        return {
            "status": status,
            "questions_prepared": qp,
            "topic_hint": th,
            "comment": comment
            or "Экзамен подготовлен. Перейди на страницу «Тесты», чтобы его пройти.",
        }
    except Exception as e:
        print(f"[ExaminerAgent] failed: {e}")
        try:
            data = examiner.generate_exam(count=safe_count, student_id=student_id)
            try:
                examiner.set_prepared_exam(student_id, data)  # type: ignore[attr-defined]
            except Exception as e2:
                print(f"[ExaminerAgent] fallback set_prepared_exam failed: {e2}")
            questions = data.get("questions") or []
            return {
                "status": "ok",
                "questions_prepared": len(questions),
                "topic_hint": default_topic_hint,
                "comment": "ExaminerAgent не сработал, тест подготовлен напрямую.",
            }
        except Exception as e2:
            return {
                "status": "error",
                "questions_prepared": 0,
                "topic_hint": default_topic_hint,
                "comment": f"Не удалось подготовить экзамен: {e2}",
            }
