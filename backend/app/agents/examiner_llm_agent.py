from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.agents import examiner

# Пытаемся импортировать новый LangChain-стек
try:
    from langchain_openai import ChatOpenAI  # type: ignore
    from langchain.tools import tool as lc_tool  # type: ignore
    from langchain.agents import create_agent  # type: ignore

    _LC_IMPORT_ERROR: Optional[Exception] = None
    print("[ExaminerAgent] LangChain imports OK")
except Exception as e:
    ChatOpenAI = None  # type: ignore
    lc_tool = None  # type: ignore
    create_agent = None  # type: ignore
    _LC_IMPORT_ERROR = e
    print(f"[ExaminerAgent] LangChain import error: {repr(e)}")


def _coerce_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value)]


def _fallback_exam(
    student_id: str,
    safe_count: int,
    topic_hint: Optional[str],
    reason: str,
) -> Dict[str, Any]:
    """
    Фолбэк-режим без LangChain:
    просто генерируем экзамен и сохраняем через examiner.
    """
    print(f"[ExaminerAgent] using fallback exam generation, reason={reason}")
    try:
        data = examiner.generate_exam(count=safe_count, student_id=student_id)

        # пытаемся сохранить предгенерированный экзамен
        try:
            examiner.set_prepared_exam(student_id, data)  # type: ignore[attr-defined]
        except Exception as e:
            print(f"[ExaminerAgent] fallback set_prepared_exam failed: {e}")

        questions = data.get("questions") or []
        return {
            "status": "ok",
            "questions_prepared": len(questions),
            "topic_hint": topic_hint,
            "comment": "Экзамен подготовлен в режиме фолбэка. Перейди на страницу «Тесты», чтобы его пройти.",
        }
    except Exception as e:
        print(f"[ExaminerAgent] fallback generate_exam failed: {e}")
        return {
            "status": "error",
            "questions_prepared": 0,
            "topic_hint": topic_hint,
            "comment": f"Не удалось подготовить экзамен: {e}",
        }


def run_examiner_agent(
    student_id: str,
    profile: Dict[str, Any],
    count: Optional[int] = None,
    topic_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ExaminerAgent: отдельный агент-Экзаменатор.

    Режимы:
    - Если LangChain/LLM недоступны → простой фолбэк: generate_exam + set_prepared_exam.
    - Если доступны → агент с tool generate_exam_for_student:
        * решает, какой экзамен подготовить (кол-во вопросов, тема),
        * вызывает tool (который реально вызывает examiner.generate_exam),
        * возвращает JSON со сводкой: status / questions_prepared / topic_hint / comment.
    """
    # защита от странных значений
    try:
        safe_count = max(1, min(20, int(count))) if count is not None else 5
    except Exception:
        safe_count = 5

    topics = _coerce_list(profile.get("topics"))
    default_topic_hint = topic_hint or (topics[0] if topics else None)

    # --- ФОЛБЭК, если нет ключа или нет LangChain ---
    if (
        not settings.OPENAI_API_KEY
        or ChatOpenAI is None
        or lc_tool is None
        or create_agent is None
    ):
        return _fallback_exam(
            student_id=student_id,
            safe_count=safe_count,
            topic_hint=default_topic_hint,
            reason=f"no-llm-or-langchain (import_error={_LC_IMPORT_ERROR})",
        )

    # --- Основной путь: LangChain-агент с одним tool ---

    try:
        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )

        # tool, который реально вызывает examiner.generate_exam и сохраняет экзамен
        @lc_tool
        def generate_exam_for_student(
            count: int,
            topic_hint: Optional[str] = default_topic_hint,
        ) -> str:
            """
            generate_exam_for_student:
            Сгенерировать и сохранить тренировочный тест для текущего студента.
            Аргументы: count (1–20) и topic_hint (строка с темой/подтемой).

            ВАЖНО: Всегда явно указывай разумное значение count,
            исходя из уровня студента:
            - beginner: 3–5 вопросов
            - intermediate: 5–7
            - advanced: 7–10
            """
            try:
                try:
                    safe_c = max(1, min(20, int(count)))
                except Exception:
                    safe_c = 5

                data = examiner.generate_exam(count=safe_c, student_id=student_id)

                # сохраняем предгенерированный экзамен
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


        tools = [generate_exam_for_student]

        ctx = {
            "student_id": student_id,
            "profile": profile,
            "requested_count": safe_count,
            "requested_topic_hint": default_topic_hint,
        }

        system_prompt = (
            "Ты — ExaminerAgent, специализированный агент-Экзаменатор.\n"
            "У тебя есть профиль студента и инструмент generate_exam_for_student, "
            "который реально создаёт и сохраняет тест.\n\n"
            "Твоя задача — подготовить разумный тренировочный экзамен по нужной теме(НЕ АБСТРАКТНЫЙ!ДЛЯ ПОНИМАНИЯ ПРАКТИЧЕСКИХ ЗНАНИЙ!).\n\n"
            "Требования:\n"
            "- Вызови generate_exam_for_student РОВНО ОДИН раз.\n"
            "- ВСЕГДА явно передавай аргумент count:\n"
            "    * для новичка (beginner): 3–5 вопросов,\n"
            "    * для среднего уровня (intermediate): 5–7 вопросов,\n"
            "    * для продвинутого (advanced): 7–10 вопросов.\n"
            "- Не полагайся на значение requested_count, выбирай count сам из диапазона выше.\n\n"
            "Финальный ответ верни строго в формате JSON без пояснений вокруг:\n"
            "{\n"
            '  \"status\": \"ok\" | \"error\",\n'
            '  \"questions_prepared\": (от 3 до 10),\n'
            '  \"topic_hint\": \"строка с темой\",\n'
            '  \"comment\": \"краткое пояснение, какой тест подготовлен\"\n'
            "}\n"
        )


        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        instructions = (
            "Подготовь экзамен для студента и верни только JSON в указанном формате.\n\n"
            "Данные студента:\n"
            f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        print("[ExaminerAgent] calling agent.invoke()...")
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

        # ----- Вынимаем текст из результата -----

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
            "[ExaminerAgent] RAW AGENT OUTPUT (first 300 chars): "
            f"{repr(raw_output)[:300]}"
        )

        # ----- Чистим обёртку ```json ... ``` -----

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
            raise ValueError(f"ExaminerAgent: no-json-in-output: {cleaned[:200]}")

        payload = cleaned[start : end + 1]
        data = json.loads(payload)

        status = str(data.get("status") or "ok")
        try:
            qp = int(data.get("questions_prepared") or 0)
        except Exception:
            qp = 0
        th = data.get("topic_hint") or default_topic_hint
        comment = str(data.get("comment") or "").strip()

        if not comment:
            comment = "Экзамен подготовлен. Перейди на страницу «Тесты», чтобы его пройти."

        return {
            "status": status,
            "questions_prepared": qp,
            "topic_hint": th,
            "comment": comment,
        }

    except Exception as e:
        print(f"[ExaminerAgent] ERROR in LC-agent: {e}")
        # если агент сломался — честно валимся в фолбэк
        return _fallback_exam(
            student_id=student_id,
            safe_count=safe_count,
            topic_hint=default_topic_hint,
            reason=f"lc-agent-error: {e}",
        )
