from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.deps import settings
from app.memory.vector_store_pg import get_last_curator_snapshot, fetch_recent_memory
from app.agents import (
    curator_llm_agent,
    examiner_llm_agent,
    materials_llm_agent,
    materials_agent,
)

# Пытаемся импортировать LangChain; если не получится — будет фолбэк-план
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


def _normalize_level(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"beginner", "intermediate", "advanced"}:
        return v
    if "нач" in v:
        return "beginner"
    if "сред" in v:
        return "intermediate"
    if "прод" in v:
        return "advanced"
    return "beginner"


def _coerce_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value)]


def _fallback_plan(student_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Простой детерминированный план без LLM / LangChain.
    Не вызывает под-агентов, только выдаёт шаги.
    """
    level = _normalize_level(str(profile.get("level") or "beginner"))
    topics = _coerce_list(profile.get("topics"))
    weaknesses = _coerce_list(profile.get("weaknesses"))

    topic_str = ", ".join(topics) if topics else "текущей теме"
    weak_str = ", ".join(weaknesses) if weaknesses else "основным пробелам"

    steps: List[Dict[str, Any]] = []

    if level in {"beginner", "intermediate"}:
        steps.append(
            {
                "id": "step_1",
                "type": "materials",
                "title": "Разобрать материалы по теме",
                "description": (
                    f"Открой раздел «Материалы» и изучи конспект по {topic_str}, "
                    f"особое внимание удели: {weak_str}."
                ),
                "meta": {
                    "student_id": student_id,
                    "level": level,
                    "topics": topics,
                    "weaknesses": weaknesses,
                },
                "status": "pending",
            }
        )
        steps.append(
            {
                "id": "step_2",
                "type": "exam",
                "title": "Потренироваться на задачах",
                "description": (
                    f"После материалов перейди во вкладку «Тесты» и реши тренировочный тест по {topic_str}."
                ),
                "meta": {
                    "student_id": student_id,
                    "level": level,
                    "topics": topics,
                },
                "status": "pending",
            }
        )
    else:
        steps.append(
            {
                "id": "step_1",
                "type": "exam",
                "title": "Сразу потренироваться на задачах",
                "description": (
                    f"Перейди во вкладку «Тесты» и реши продвинутый тест по теме {topic_str}."
                ),
                "meta": {
                    "student_id": student_id,
                    "level": level,
                    "topics": topics,
                },
                "status": "pending",
            }
        )
        steps.append(
            {
                "id": "step_2",
                "type": "materials",
                "title": "Добить пробелы по теории",
                "description": (
                    f"После теста открой раздел «Материалы» и разберись с пробелами: {weak_str}."
                ),
                "meta": {
                    "student_id": student_id,
                    "level": level,
                    "weaknesses": weaknesses,
                },
                "status": "pending",
            }
        )

    instruction = (
        "Я составил для тебя простой план:\n"
        "1) Разберись с теорией и примерами в разделе «Материалы».\n"
        "2) Затем реши тренировочный тест во вкладке «Тесты».\n"
        "После этого можно снова обратиться к куратору, чтобы скорректировать план."
    )

    return {"instruction_message": instruction, "plan_steps": steps}


def _build_tools(student_id: str, profile: Dict[str, Any]) -> List["Tool"]:
    """
    Собираем набор инструментов LangChain вокруг отдельных специализированных агентов:
    - curator_agent      → CuratorAgent (анализ профиля/памяти)
    - examiner_agent     → ExaminerAgent (подготовка экзаменов)
    - materials_agent    → MaterialsAgent (генерация материалов)
    а также несколько вспомогательных инструментов для работы с памятью/материалами.
    """
    if Tool is None:
        return []

    tools: List[Tool] = []

    # --- CuratorAgent: анализирует профиль и память, даёт резюме и приоритетные темы ---
    def _tool_curator_agent(task: str = "") -> str:
        """
        Вызвать CuratorAgent. Он вернёт summary, recommended_topics, notes.
        """
        try:
            result = curator_llm_agent.run_curator_agent(
                student_id=student_id,
                profile=profile,
                task=task,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_curator_agent,
            name="curator_agent",
            description=(
                "Обратиться к специализированному CuratorAgent. "
                "Он анализирует профиль и недавнюю активность студента и возвращает резюме "
                "и список приоритетных тем. Аргумент: task (строка с задачей/вопросом)."
            ),
        )
    )

    # --- ExaminerAgent: готовит и сохраняет экзамен для студента ---
    def _tool_examiner_agent(count: int = 5, topic_hint: Optional[str] = None) -> str:
        """
        Вызвать ExaminerAgent. Он подготовит персональный экзамен и сохранит его.
        """
        try:
            result = examiner_llm_agent.run_examiner_agent(
                student_id=student_id,
                profile=profile,
                count=count,
                topic_hint=topic_hint,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_examiner_agent,
            name="examiner_agent",
            description=(
                "Обратиться к специализированному ExaminerAgent. "
                "Он генерирует персональный тренировочный тест и сохраняет его для страницы «Тесты». "
                "Аргументы: count (int, 1–20) — желаемое число вопросов, "
                "topic_hint (строка) — тема/подтема, на которой сделать акцент."
            ),
        )
    )

    # --- MaterialsAgent: генерирует и сохраняет учебные материалы ---
    def _tool_materials_agent(
        focus_topics: Optional[List[str]] = None,
        weaknesses: Optional[List[str]] = None,
    ) -> str:
        """
        Вызвать MaterialsAgent. Он создаёт и сохраняет материалы.
        """
        try:
            result = materials_llm_agent.run_materials_agent(
                student_id=student_id,
                profile=profile,
                focus_topics=focus_topics,
                weaknesses=weaknesses,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_materials_agent,
            name="materials_agent",
            description=(
                "Обратиться к специализированному MaterialsAgent. "
                "Он создаёт и сохраняет конспекты, шпаргалки и ссылки по темам студента. "
                "Аргументы: focus_topics (список тем) и weaknesses (список слабых мест)."
            ),
        )
    )

    # --- Вспомогательные инструменты работы с памятью/материалами (сенсоры) ---

    def _tool_get_materials_summary(limit: int = 5) -> str:
        """
        Вернуть краткое описание уже существующих материалов студента.
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
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_get_materials_summary,
            name="get_materials_summary",
            description=(
                "Получить краткий список уже существующих материалов для студента "
                "(заголовок, тип). Полезно, чтобы не генерировать лишнее."
            ),
        )
    )

    def _tool_get_student_profile() -> str:
        """
        Вернуть последний сохранённый срез куратора из памяти.
        """
        try:
            snap = get_last_curator_snapshot(student_id)
            return json.dumps({"status": "ok", "snapshot": snap}, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_get_student_profile,
            name="get_student_profile",
            description=(
                "Получить последний сохранённый срез куратора (профиль студента) "
                "из долговременной памяти."
            ),
        )
    )

    def _tool_get_recent_memory(limit: int = 5) -> str:
        """
        Вернуть несколько последних записей из памяти студента.
        """
        try:
            recs = fetch_recent_memory(
                student_id=student_id,
                kind=None,
                limit=max(1, min(20, int(limit))),
            )
            return json.dumps({"status": "ok", "records": recs}, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(
        Tool.from_function(
            func=_tool_get_recent_memory,
            name="get_recent_memory",
            description=(
                "Получить несколько последних записей из памяти студента "
                "(например, его ответы, заметки, предыдущие объяснения)."
            ),
        )
    )

    return tools


def _agent_plan(student_id: str, profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Построение плана через настоящего LangChain-агента с tools.
    При любой ошибке возвращает None, чтобы наверху сработал фолбэк.
    """
    if not settings.OPENAI_API_KEY:
        return None
    if ChatOpenAI is None or initialize_agent is None or AgentType is None or Tool is None:
        # LangChain или langchain-openai недоступны
        return None

    try:
        level = _normalize_level(str(profile.get("level") or "beginner"))
        topics = _coerce_list(profile.get("topics"))
        weaknesses = _coerce_list(profile.get("weaknesses"))
        goals = _coerce_list(
            profile.get("goals") or profile.get("target") or profile.get("targets")
        )

        # немного контекста из памяти/среза
        try:
            snap = get_last_curator_snapshot(student_id)
        except Exception as e:
            print(f"[orchestrator] get_last_curator_snapshot failed: {e}")
            snap = None

        try:
            recent = fetch_recent_memory(
                student_id=student_id,
                kind=None,
                limit=5,
            )
        except Exception as e:
            print(f"[orchestrator] fetch_recent_memory failed: {e}")
            recent = []

        ctx = {
            "student_id": student_id,
            "level": level,
            "goals": goals,
            "topics": topics,
            "weaknesses": weaknesses,
            "raw_profile": profile,
            "last_curator_snapshot": snap,
            "recent_memory": recent,
        }

        model_name = getattr(settings, "ORCHESTRATOR_MODEL", None) or getattr(
            settings, "OPENAI_MODEL", "gpt-4o-mini"
        )

        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=model_name,
            temperature=0.2,
        )

        tools = _build_tools(student_id, profile)

        max_steps = getattr(settings, "ORCHESTRATOR_MAX_STEPS", 4)
        try:
            max_steps_int = int(max_steps)
        except Exception:
            max_steps_int = 4

        agent = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=False,
            max_iterations=max_steps_int,
            handle_parsing_errors=True,
        )

        # Инструкция агенту: что делать и в каком формате вернуть финальный ответ
        instructions = (
            "Ты — главный учебный координатор (оркестратор) для студента.\n"
            "У тебя есть профиль ученика и несколько специализированных агентов, "
            "с которыми ты можешь общаться через инструменты (tools):\n"
            "- curator_agent      → CuratorAgent, анализирует профиль и память;\n"
            "- examiner_agent     → ExaminerAgent, готовит персональные тесты;\n"
            "- materials_agent    → MaterialsAgent, создаёт учебные материалы;\n"
            "а также вспомогательные инструменты get_materials_summary, get_student_profile, get_recent_memory.\n\n"
            "Твои задачи:\n"
            "1) Проанализировать уровень, цели, темы и слабые места студента.\n"
            "2) При необходимости обратиться к специализированным агентам через соответствующие tools.\n"
            "3) Составить понятный и не слишком длинный план из 2–4 шагов, который использует разделы интерфейса:\n"
            "   - вкладка «Тесты» (экзаменатор),\n"
            "   - раздел «Материалы»,\n"
            "   - при желании — возвращение к Куратору (чат).\n\n"
            "Ограничения:\n"
            "- Не делай больше нескольких вызовов инструментов — только когда они реально помогают плану.\n"
            "- Обязательно включи хотя бы один шаг с type=\"exam\" или type=\"materials\".\n"
            "- Всего шагов в плане должно быть не более 4.\n\n"
            "Финальный ответ:\n"
            "- В качестве финального ответа ты ДОЛЖЕН вывести ТОЛЬКО JSON без пояснений вокруг, в формате:\n"
            "{\n"
            '  \"instruction_message\": \"краткий понятный текст-пояснение студенту, что он будет делать дальше\",\n'
            "  \"plan_steps\": [\n"
            "    {\n"
            '      \"id\": \"step_1\",\n'
            "      \"type\": \"exam\" | \"materials\" | \"chat\" | \"other\",\n"
            '      \"title\": \"краткий заголовок шага\",\n'
            '      \"description\": \"что именно студент должен сделать (со ссылкой на раздел интерфейса)\",\n'
            "      \"meta\": {\"any\": \"дополнительные данные по желанию\"},\n"
            '      \"status\": \"prepared\" | \"pending\" | \"error\"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Где:\n"
            "- type отражает раздел интерфейса (exam = «Тесты», materials = «Материалы», chat = вернуться к Куратору).\n"
            "- Если ты вызывал инструменты для подготовки теста/материалов, проставь status=\"prepared\" и добавь в meta краткую сводку.\n"
            "- Если инструмент завершился с ошибкой — status=\"error\" и meta.error с кратким описанием.\n\n"
            "Теперь внимательно изучи данные ниже и при необходимости используй инструменты. "
            "После этого верни только итоговый JSON-план в указанном формате.\n\n"
            f"Данные профиля студента:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        raw_output = agent.run(instructions)
        if not isinstance(raw_output, str):
            raw_output = str(raw_output)

        # Вырезаем JSON из ответа (на случай, если модель что-то добавила вокруг)
        start = raw_output.find("{")
        end = raw_output.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no-json-in-agent-output")

        payload = raw_output[start : end + 1]
        data = json.loads(payload)

        if "instruction_message" not in data or "plan_steps" not in data:
            raise ValueError("bad-agent-json-structure")

        # Нормализуем список шагов
        steps_raw = data.get("plan_steps") or []
        steps: List[Dict[str, Any]] = []
        for idx, step in enumerate(steps_raw):
            if not isinstance(step, dict):
                continue
            stype = str(step.get("type") or "other").lower()
            if stype not in {"exam", "materials", "chat", "other"}:
                stype = "other"

            status = str(step.get("status") or "pending").lower()
            if status not in {"prepared", "pending", "error"}:
                status = "pending"

            steps.append(
                {
                    "id": str(step.get("id") or f"step_{idx+1}"),
                    "type": stype,
                    "title": str(step.get("title") or f"Шаг {idx+1}"),
                    "description": str(step.get("description") or "").strip()
                    or "Сделай этот шаг в интерфейсе обучения.",
                    "meta": step.get("meta") or {},
                    "status": status,
                }
            )

        if not steps:
            raise ValueError("empty-steps")

        return {
            "instruction_message": str(data.get("instruction_message") or "").strip(),
            "plan_steps": steps,
        }
    except Exception as e:
        print(f"[orchestrator] agent planning failed: {e}")
        return None


async def plan_and_execute(student_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Главная функция оркестратора.

    1) Пытается построить план через LangChain-агента с tools.
    2) При любой проблеме использует простой фолбэк-план.
    3) Нормализует шаги.
    4) ЯВНО указывает, к какому агенту и на какой route фронту имеет смысл
       автоматически перевести пользователя (next_agent, auto_route, primary_step_id).
    """
    plan = _agent_plan(student_id, profile)
    if plan is None:
        plan = _fallback_plan(student_id, profile)

    instruction = str(plan.get("instruction_message") or "").strip()
    if not instruction:
        instruction = (
            "Я составил для тебя план: сначала разберись с теорией в разделе «Материалы», "
            "потом реши тренировочный тест во вкладке «Тесты»."
        )

    raw_steps = plan.get("plan_steps") or []
    normalized_steps: List[Dict[str, Any]] = []

    for idx, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            continue
        stype = str(step.get("type") or "other").lower()
        if stype not in {"exam", "materials", "chat", "other"}:
            stype = "other"

        status = str(step.get("status") or "pending").lower()
        if status not in {"prepared", "pending", "error"}:
            status = "pending"

        meta = step.get("meta") or {}

        normalized_steps.append(
            {
                "id": str(step.get("id") or f"step_{idx+1}"),
                "type": stype,
                "title": str(step.get("title") or f"Шаг {idx+1}"),
                "description": str(step.get("description") or "").strip()
                or "Сделай этот шаг в интерфейсе обучения.",
                "meta": meta,
                "status": status,
            }
        )

    # --- Определяем «главный» шаг и следующего агента/route ---

    primary_step: Optional[Dict[str, Any]] = None
    for step in normalized_steps:
        # Не берём шаги с ошибкой
        if step.get("status") == "error":
            continue
        # Интересны только реальные агенты: экзаменатор, материалы или чат
        if step.get("type") in {"exam", "materials", "chat"}:
            primary_step = step
            break

    next_agent = "none"
    auto_route: Optional[str] = None
    primary_step_id: Optional[str] = None

    if primary_step is not None:
        primary_step_id = str(primary_step.get("id"))
        stype = primary_step.get("type")

        if stype == "exam":
            # Главным агентом становится Exam и фронт может сразу открыть /tests
            next_agent = "examiner"
            auto_route = "/tests"
        elif stype == "materials":
            # Главный агент — материалы → можно открыть /materials
            next_agent = "materials"
            auto_route = "/materials"
        elif stype == "chat":
            # Остаёмся в кураторе (страница чата)
            next_agent = "curator"
            auto_route = None

    return {
        "instruction_message": instruction,
        "plan_steps": normalized_steps,
        "next_agent": next_agent,
        "auto_route": auto_route,
        "primary_step_id": primary_step_id,
    }
