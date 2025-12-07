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

# ==== LangChain: новый API (v1) ====
try:
    from langchain_openai import ChatOpenAI  # type: ignore
    from langchain.tools import tool as lc_tool  # type: ignore
    from langchain.agents import create_agent  # type: ignore
except Exception as e:
    ChatOpenAI = None  # type: ignore
    lc_tool = None  # type: ignore
    create_agent = None  # type: ignore
    _LC_IMPORT_ERROR = e
    print(f"[orchestrator] LangChain import error: {repr(e)}")
else:
    _LC_IMPORT_ERROR = None
    print("[orchestrator] LangChain imports OK")


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
    print("[orchestrator] Работает fallback_plan (без LangChain)")
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


def _build_tools(student_id: str, profile: Dict[str, Any]) -> List[Any]:
    """
    Собираем набор инструментов вокруг отдельных специализированных агентов.
    Используем декоратор @tool (langchain.tools.tool) БЕЗ name/description.
    """
    if lc_tool is None:
        print("[orchestrator._build_tools] lc_tool is None → tools=[]")
        return []

    tools: List[Any] = []

    # --- CuratorAgent ---
    @lc_tool
    def curator_agent(task: str = "") -> str:
        """
        CuratorAgent:
        Анализирует профиль и недавнюю активность студента и возвращает резюме
        и список приоритетных тем. Аргумент: task (строка с задачей/вопросом).
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

    tools.append(curator_agent)

    # --- ExaminerAgent ---
    @lc_tool
    def examiner_agent(count: int = 5, topic_hint: Optional[str] = None) -> str:
        """
        ExaminerAgent:
        Генерирует персональный тренировочный тест и сохраняет его для страницы «Тесты».
        Аргументы: count (1–20), topic_hint — тема/подтема, на которой сделать акцент.
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

    tools.append(examiner_agent)

    # --- MaterialsAgent ---
    @lc_tool
    def materials_agent_tool(
        focus_topics: Optional[List[str]] = None,
        weaknesses: Optional[List[str]] = None,
    ) -> str:
        """
        MaterialsAgent:
        Создаёт и сохраняет конспекты, шпаргалки и ссылки по темам студента.
        Аргументы: focus_topics (список тем), weaknesses (список слабых мест).
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

    tools.append(materials_agent_tool)

    # --- Сенсор: краткий список материалов ---
    @lc_tool
    def get_materials_summary(limit: int = 5) -> str:
        """
        get_materials_summary:
        Возвращает краткий список уже существующих материалов (id, title, type)
        для текущего студента. Ограничение по числу элементов — limit (1–20).
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

    tools.append(get_materials_summary)

    # --- Сенсор: последний срез профиля ---
    @lc_tool
    def get_student_profile() -> str:
        """
        get_student_profile:
        Возвращает последний сохранённый срез куратора (профиль студента)
        из долговременной памяти.
        """
        try:
            snap = get_last_curator_snapshot(student_id)
            return json.dumps({"status": "ok", "snapshot": snap}, ensure_ascii=False)
        except Exception as e:
            err = {"status": "error", "error": str(e)}
            return json.dumps(err, ensure_ascii=False)

    tools.append(get_student_profile)

    # --- Сенсор: свежая память ---
    @lc_tool
    def get_recent_memory(limit: int = 5) -> str:
        """
        get_recent_memory:
        Возвращает несколько последних записей из памяти студента
        (его ответы, заметки, предыдущие объяснения и т.п.).
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

    tools.append(get_recent_memory)

    return tools


def _agent_plan(
    student_id: str,
    profile: Dict[str, Any],
    chat_messages: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Строим план через LangChain-агента (новый API create_agent).
    При любой ошибке возвращаем None → сверху сработает fallback.
    """
    if not settings.OPENAI_API_KEY:
        print("[orchestrator._agent_plan] OPENAI_API_KEY is empty → None")
        return None
    if ChatOpenAI is None or lc_tool is None or create_agent is None:
        print(f"[orchestrator._agent_plan] LangChain not available, import_error={_LC_IMPORT_ERROR}")
        return None

    try:
        level = _normalize_level(str(profile.get("level") or "beginner"))
        topics = _coerce_list(profile.get("topics"))
        weaknesses = _coerce_list(profile.get("weaknesses"))
        goals = _coerce_list(
            profile.get("goals") or profile.get("target") or profile.get("targets")
        )

        try:
            snap = get_last_curator_snapshot(student_id)
        except Exception as e:
            print(f"[orchestrator] get_last_curator_snapshot failed: {e}")
            snap = None

        try:
            recent = fetch_recent_memory(
                student_id=student_id,
                kind=None,
                limit=8,
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
            "chat_messages": chat_messages or [],  # ← добавили
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

        # system_prompt — общая инструкция агенту
        system_prompt = (
            "Ты — главный учебный координатор (оркестратор) для студента.\n"
            "У тебя есть профиль ученика и несколько специализированных агентов, "
            "с которыми ты можешь общаться через инструменты (tools):\n"
            "- curator_agent      → анализирует профиль и память;\n"
            "- examiner_agent     → готовит персональные тесты;\n"
            "- materials_agent    → создаёт учебные материалы;\n"
            "а также вспомогательные инструменты get_materials_summary, "
            "get_student_profile, get_recent_memory.\n\n"
            "Твоя задача — построить учебный план из 2–4 шагов.\n"
            "Финальный ответ ДОЛЖЕН быть строго в формате JSON (см. далее)."
        )

        # создаём агента нового типа
        agent = create_agent(llm, tools=tools, system_prompt=system_prompt)

        # Пользовательское сообщение с форматом JSON и данными профиля
        instructions = (
            "Составь учебный план.\n\n"
            "Формат ответа (строго JSON, без пояснений вокруг):\n"
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
            "Данные профиля студента:\n"
            f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
        )

        print("[orchestrator._agent_plan] calling agent.invoke()...")
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

        # В новом API результат — словарь с 'messages'
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
            # может вернуться список блоков {type: "text", text: "..."}
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
            "[orchestrator._agent_plan] RAW AGENT OUTPUT (first 300 chars): "
            f"{repr(raw_output)[:300]}"
        )

        # --- очистка от ```json ... ```
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
            raise ValueError(f"no-json-in-agent-output: {cleaned[:200]}")

        payload = cleaned[start : end + 1]
        data = json.loads(payload)

        if "instruction_message" not in data or "plan_steps" not in data:
            raise ValueError("bad-agent-json-structure")

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
        print(f"[orchestrator._agent_plan] ERROR: {e}")
        return None


async def plan_and_execute(
    student_id: str,
    profile: Dict[str, Any],
    chat_messages: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Главная функция оркестратора.

    1) Пытается построить план через LangChain-агента с tools.
    2) При любой проблеме использует простой фолбэк-план.
    3) Нормализует шаги.
    4) ЯВНО указывает, к какому агенту и на какой route фронту имеет смысл
       автоматически перевести пользователя (next_agent, auto_route, primary_step_id).
    """
    plan = _agent_plan(student_id, profile, chat_messages=chat_messages)
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

    # --- дальше твоя логика выбора primary_step / next_agent / auto_route как у тебя написано ---
    primary_step: Optional[Dict[str, Any]] = None
    for step in normalized_steps:
        if step.get("status") == "error":
            continue
        if step.get("type") in {"exam", "materials", "chat"}:
            primary_step = step
            break

    raw_next_agent = str(plan.get("next_agent") or "").strip().lower()
    if raw_next_agent not in {"examiner", "materials", "curator", "none"}:
        raw_next_agent = "none"

    raw_auto_route = plan.get("auto_route")
    if isinstance(raw_auto_route, str):
        raw_auto_route = raw_auto_route.strip() or None
    else:
        raw_auto_route = None

    next_agent = raw_next_agent or "none"
    auto_route: Optional[str] = raw_auto_route
    primary_step_id: Optional[str] = None

    if primary_step is not None:
        primary_step_id = str(primary_step.get("id"))
        stype = primary_step.get("type")

        if next_agent == "none":
            if stype == "exam":
                next_agent = "examiner"
            elif stype == "materials":
                next_agent = "materials"
            elif stype == "chat":
                next_agent = "curator"

        if auto_route is None:
            if stype == "exam":
                auto_route = "/tests"
            elif stype == "materials":
                auto_route = "/materials"
            elif stype == "chat":
                auto_route = None

    return {
        "instruction_message": instruction,
        "plan_steps": normalized_steps,
        "next_agent": next_agent,
        "auto_route": auto_route,
        "primary_step_id": primary_step_id,
    }

