# app/agents/materials_llm_agent.py
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
    """
    Нормализуем поле профиля к списку строк:
    - None → []
    - "строка" → ["строка"]
    - [..] → список строк без пустых.
    """
    if not value:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()]


def _fallback_materials(
    student_id: str,
    topics: List[str],
    weaknesses: List[str],
    reason: str,
) -> Dict[str, Any]:
    """
    Фолбэк-режим без LangChain или при ошибке агента:
    просто генерируем/обновляем материалы и возвращаем сводку.
    """
    print(f"[MaterialsAgent] using fallback materials generation, reason={reason}")
    try:
        mats = materials_agent.generate_and_save_materials(student_id=student_id)
        m_count = len(mats or [])

        comment_parts: List[str] = []
        if m_count > 0:
            comment_parts.append(f"Подготовлено материалов: {m_count}.")
        if topics:
            comment_parts.append("Фокус по темам: " + ", ".join(topics[:3]) + ".")
        if weaknesses:
            comment_parts.append("Особое внимание на: " + ", ".join(weaknesses[:3]) + ".")
        comment = (
            " ".join(comment_parts)
            or "Материалы обновлены. Открой раздел «Материалы», чтобы их посмотреть."
        )

        return {
            "status": "ok",
            "materials_prepared": m_count,
            "focus_topics": topics,
            "weaknesses": weaknesses,
            "comment": comment,
            "study_suggestions": [
                "1) Открой раздел «Материалы» и начни с конспектов.",
                "2) Затем посмотри шпаргалки по своим слабым местам.",
                "3) В конце пройди тесты, чтобы закрепить знания.",
            ],
        }
    except Exception as e:
        print(f"[MaterialsAgent] fallback generate_and_save_materials failed: {e}")
        return {
            "status": "error",
            "materials_prepared": 0,
            "focus_topics": topics,
            "weaknesses": weaknesses,
            "comment": f"Не удалось подготовить материалы: {e}",
            "study_suggestions": [],
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

    Возвращает JSON:
    {
      "status": "ok" | "error",
      "materials_prepared": int,
      "focus_topics": [...],
      "weaknesses": [...],
      "comment": "человеческое пояснение",
      "study_suggestions": ["1) ...", "2) ..."]
    }
    """
    topics = focus_topics or _coerce_list(profile.get("topics"))
    weak = weaknesses or _coerce_list(profile.get("weaknesses"))

    topics = topics[:5]
    weak = weak[:5]

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
        def get_materials_summary(limit: int = 8) -> str:
            """
            get_materials_summary:
            Вернуть краткий список уже существующих материалов студента.
            Возвращает JSON {status, materials: [{title, type, has_url}]}.
            """
            try:
                mats = materials_agent.get_materials_for_student(student_id=student_id)
                items = mats[: max(1, min(20, int(limit)))]
                simplified = [
                    {
                        "title": m.get("title"),
                        "type": m.get("type"),
                        "has_url": bool(m.get("url")),
                    }
                    for m in items
                ]
                return json.dumps(
                    {"status": "ok", "materials": simplified},
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)},
                    ensure_ascii=False,
                )

        # tool: сгенерировать/обновить материалы
        @lc_tool
        def generate_materials_for_student() -> str:
            """
            generate_materials_for_student:
            Сгенерировать и сохранить учебные материалы (конспекты, шпаргалки, ссылки)
            для текущего студента.
            Возвращает JSON {status, materials_prepared, materials: [{title, type, has_url}]}.
            """
            try:
                mats = materials_agent.generate_and_save_materials(
                    student_id=student_id
                )
                all_mats = materials_agent.get_materials_for_student(
                    student_id=student_id
                )
                simplified = [
                    {
                        "title": m.get("title"),
                        "type": m.get("type"),
                        "has_url": bool(m.get("url")),
                    }
                    for m in all_mats
                ]
                return json.dumps(
                    {
                        "status": "ok",
                        "materials_prepared": len(mats or []),
                        "materials": simplified,
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"status": "error", "error": str(e)},
                    ensure_ascii=False,
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
            "\n"
            "Твой контекст:\n"
            "- У студента есть профиль (уровень, темы, слабые места).\n"
            "- В базе уже могут лежать конспекты, шпаргалки и ссылки, "
            "которые генерирует другой агент (materials_agent).\n"
            "\n"
            "Твои инструменты (tools):\n"
            "1) get_materials_summary(limit:int)\n"
            "   → смотреть, какие материалы уже есть (title, type, has_url).\n"
            "2) generate_materials_for_student()\n"
            "   → попросить низкоуровневый агент materials_agent сгенерировать/обновить материалы,\n"
            "     после чего получить полный список материалов.\n"
            "\n"
            "Как действовать:\n"
            "- Сначала почти всегда полезно один раз вызвать get_materials_summary, "
            "  чтобы понять, что уже есть.\n"
            "- Если у студента НЕТ материалов нужных типов (notes/cheat_sheet/link) "
            "  или явно поменялись слабые места, вызови generate_materials_for_student.\n"
            "- Избегай лишних вызовов инструментов: максимум по одному разу каждый.\n"
            "- Особый акцент делай на темах из focus_topics и слабых местах.\n"
            "\n"
            "Финальный ответ верни строго в формате JSON без пояснений вокруг:\n"
            "{\n"
            '  \"status\": \"ok\" | \"error\",\n'
            '  \"materials_prepared\": 3,\n'
            '  \"focus_topics\": [\"тема1\", \"тема2\"],\n'
            '  \"weaknesses\": [\"слабое место1\"],\n'
            '  \"comment\": \"краткое пояснение, какие материалы и зачем подготовлены\",\n'
            '  \"study_suggestions\": [\n'
            '    \"1) Сначала открой такой-то конспект...\",\n'
            '    \"2) Затем посмотри такую-то шпаргалку...\"\n'
            "  ]\n"
            "}\n"
            "\n"
            "Говори по-русски, без лишней воды. JSON должен быть единственным содержимым ответа."
        )

        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        instructions = (
            "Определи, какие материалы нужны студенту, при необходимости обнови их "
            "и верни только JSON в указанном формате.\n\n"
            "Данные студента (для контекста, не надо механически переписывать их в ответ):\n"
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
        suggestions = data.get("study_suggestions") or []

        if not isinstance(f_topics, list):
            f_topics = _coerce_list(f_topics)
        if not isinstance(weak2, list):
            weak2 = _coerce_list(weak2)
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]

        return {
            "status": status,
            "materials_prepared": mp,
            "focus_topics": f_topics,
            "weaknesses": weak2,
            "comment": comment
            or "Материалы обновлены. Открой раздел «Материалы», чтобы их посмотреть.",
            "study_suggestions": [str(x) for x in suggestions if str(x).strip()],
        }

    except Exception as e:
        print(f"[MaterialsAgent] ERROR in LC-agent: {e}")
        return _fallback_materials(
            student_id=student_id,
            topics=topics,
            weaknesses=weak,
            reason=f"lc-agent-error: {e}",
        )
