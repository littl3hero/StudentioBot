# app/agents/curator.py
import json
from typing import List

from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError

from app.deps import settings
from app.routers.legacy_api import _DB_STUDENT, StudentProfile
from app.memory.vector_store_pg import retrieve_memory, save_memory

client = OpenAI(api_key=settings.OPENAI_API_KEY)


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


def _basic_advice(errors: List[str], level: str, topic_hint: str = "") -> str:
    """Короткая шпаргалка на случай, если LLM недоступен/исчерпана квота."""
    lvl = _normalize_level(level)
    parts = []
    if topic_hint:
        parts.append(f"Тема: {topic_hint}")
    if errors:
        parts.append("Типичные ошибки: " + ", ".join(errors))

    tips = []
    low = [e.lower() for e in errors]
    if any("знак" in e for e in low):
        tips.append("Следи за знаками при переносах и раскрытии скобок.")
    if any("скоб" in e for e in low):
        tips.append("Аккуратно раскрывай скобки: a(b+c)=ab+ac; «минус» перед скобками меняет знаки внутри.")
    if any("формул" in e for e in low):
        tips.append("Собери мини-табличку формул именно для этой темы и пробеги перед решением.")
    if any("логик" in e for e in low):
        tips.append("В логике проверь приоритет операций и расставь скобки; сделай 2–3 контрольных примера.")

    if lvl == "beginner":
        tips.append("Разбивай задачу на шаги и решай 1–2 простых примера на каждый шаг.")
    elif lvl == "intermediate":
        tips.append("Пробуй сначала без шпаргалки, затем сравни и зафиксируй «узкие» места.")
    else:
        tips.append("Иди от формального определения к задаче и обратно, проверяя крайние случаи.")

    if tips:
        parts.append("Советы:\n- " + "\n- ".join(tips))
    return "\n".join(parts) if parts else "Повтори определения, выпиши ключевые формулы и реши 2–3 базовых примера."


async def assess_student(goals: str, errors: list[str], level: str, student_id: str = "default") -> dict:
    """
    Анализирует данные ученика и обновляет его профиль, используя контекст из памяти.
    - Тянет похожие записи из памяти (pgvector/trigram/last-resort — внутри vector_store_pg есть фолбэки).
    - Пытается получить структурный профиль через LLM.
    - При ошибке OpenAI (в т.ч. 429 insufficient_quota) — возвращает фолбэк-профиль.
    - Всегда сохраняет «срез» в память и обновляет in-memory профиль (_DB_STUDENT).
    """
    lvl = _normalize_level(level)
    errs = [str(e).strip() for e in (errors or []) if str(e).strip()]
    goals = (goals or "").strip()

    # 1) достаём похожие прошлые данные из памяти (без срывов при отсутствии эмбеддингов)
    try:
        memory_contexts = retrieve_memory(" ".join(errs + [goals]) or "общая тема", k=3, student_id=student_id)
    except Exception as e:
        print(f"[curator] retrieve_memory failed: {e}")
        memory_contexts = []
    memory_text = "\n".join(memory_contexts) if memory_contexts else "нет предыдущих данных."

    # 2) готовим промпт для LLM
    prompt = f"""
Ты — Куратор. На основе данных оцени профиль ученика.
Учитывай прошлый опыт обучения:
{memory_text}

Текущие данные:
Цели: {goals or "—"}
Ошибки: {', '.join(errs) if errs else "—"}
Самооценка уровня: {lvl}

Ответ строго в JSON:
{{
  "profile": {{
     "level": "beginner|intermediate|advanced",
     "strengths": ["..."],
     "weaknesses": ["..."],
     "topics": ["..."],
     "notes": "...",
     "advice": "короткая шпаргалка по исправлению типичных ошибок"
  }}
}}
"""

    profile_data: dict
    # 3) пробуем LLM; ловим типовые ошибки квоты/сети/JSON-парсинга
    if settings.OPENAI_API_KEY:
        try:
            chat = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": "Ты опытный учебный куратор. Отвечай строго в JSON."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = chat.choices[0].message.content or "{}"
            data = json.loads(content)
            profile_data = data.get("profile", {})
            # подстрахуем поля
            profile_data.setdefault("level", lvl)
            profile_data.setdefault("strengths", [])
            profile_data.setdefault("weaknesses", errs or ["ошибки не указаны"])
            profile_data.setdefault("topics", [goals] if goals else ["основы предмета"])
            profile_data.setdefault("notes", "")
            profile_data.setdefault("advice", _basic_advice(errs, lvl, goals))
        except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
            print(f"[curator] LLM API error: {e}")
            profile_data = {
                "level": lvl,
                "strengths": [],
                "weaknesses": errs or ["ошибки не указаны"],
                "topics": [goals] if goals else ["основы предмета"],
                "notes": "LLM недоступен (квота/сеть). Применена эвристика.",
                "advice": _basic_advice(errs, lvl, goals),
            }
        except Exception as e:
            print(f"[curator] LLM parse error: {e}")
            profile_data = {
                "level": lvl,
                "strengths": [],
                "weaknesses": errs or ["ошибки не указаны"],
                "topics": [goals] if goals else ["основы предмета"],
                "notes": "(fallback) ошибка разбора JSON",
                "advice": _basic_advice(errs, lvl, goals),
            }
    else:
        # без ключа — сразу эвристика
        profile_data = {
            "level": lvl,
            "strengths": [],
            "weaknesses": errs or ["ошибки не указаны"],
            "topics": [goals] if goals else ["основы предмета"],
            "notes": "OPENAI_API_KEY не задан — ответ без LLM.",
            "advice": _basic_advice(errs, lvl, goals),
        }

    # 4) сохраняем новое знание в память (не падает даже без эмбеддингов)
    try:
        text_for_memory = (
            "=== CURATOR ASSESSMENT ===\n"
            f"student_id: {student_id}\n"
            f"goals: {goals or '—'}\n"
            f"errors: {', '.join(errs) if errs else '—'}\n"
            f"profile: {json.dumps(profile_data, ensure_ascii=False)}"
        )
        save_memory(student_id, text_for_memory, {"level": profile_data["level"], "goals": goals, "errors": errs})
    except Exception as e:
        print(f"[curator] save_memory failed: {e}")

    # 5) обновляем in-memory профиль для совместимости со старым фронтом /student
    try:
        global _DB_STUDENT
        _DB_STUDENT = StudentProfile(
            name="",
            goals=goals,
            level=profile_data.get("level", lvl),
            notes=profile_data.get("notes", ""),
        )
    except Exception as e:
        print(f"[curator] legacy in-memory update failed: {e}")

    return profile_data