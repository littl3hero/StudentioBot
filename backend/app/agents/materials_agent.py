# app/agents/materials_agent.py
import json
from typing import List, Dict, Any, Optional
from app.deps import settings
from app.memory.vector_store_pg import get_conn, get_last_curator_snapshot
from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError


def init_materials_table():
    """Создаёт таблицу materials при запуске (если не существует)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS materials (
                    id SERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('link', 'notes', 'cheat_sheet')),
                    url TEXT,
                    content TEXT
                );
            """)


def _llm_client() -> Optional[OpenAI]:
    if not settings.OPENAI_API_KEY:
        return None
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _generate_materials_with_llm(
    student_id: str,
    level: str,
    topics: List[str],
    weaknesses: List[str]
) -> List[Dict[str, Any]]:
    """Генерирует материалы через LLM."""
    client = _llm_client()
    if not client:
        return _fallback_materials(level, topics, weaknesses)

    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
    prompt = f"""
Ты — учебный ассистент. Создай 2–3 материала под студента:
- Уровень: {level}
- Темы: {', '.join(topics) or 'базовые понятия'}
- Проблемы: {', '.join(weaknesses) or 'нет данных'}

Ответ строго в JSON:
{{
  "materials": [
    {{
      "title": "...",
      "type": "notes|cheat_sheet|link",
      "content": "... или null",
      "url": "... или null"
    }}
  ]
}}
Без пояснений.
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.5,
            messages=[
                {"role": "system", "content": "Ты генератор учебных материалов. Отвечай строго в JSON."},
                {"role": "user", "content": prompt}
            ]
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        raw = data.get("materials", [])
        return _sanitize_materials(raw)
    except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
        print(f"[materials_agent] LLM API error: {e}")
        return _fallback_materials(level, topics, weaknesses)
    except Exception as e:
        print(f"[materials_agent] LLM parse error: {e}")
        return _fallback_materials(level, topics, weaknesses)


def _sanitize_materials(raw: List[Dict]) -> List[Dict[str, Any]]:
    out = []
    for m in raw:
        typ = m.get("type", "notes")
        if typ not in {"link", "notes", "cheat_sheet"}:
            typ = "notes"
        out.append({
            "title": str(m.get("title") or "Без названия")[:100],
            "type": typ,
            "url": str(m.get("url")) if m.get("url") else None,
            "content": str(m.get("content")) if m.get("content") else None,
        })
    return out[:3]  # максимум 3 материала


def _fallback_materials(level: str, topics: List[str], weaknesses: List[str]) -> List[Dict[str, Any]]:
    """Фолбэк-материалы без LLM."""
    base = [
        {
            "title": "Общий конспект",
            "type": "notes",
            "content": "Повтори определения, выпиши формулы, реши 2–3 примера.",
            "url": None
        }
    ]
    if weaknesses:
        base.append({
            "title": "Шпаргалка по ошибкам",
            "type": "cheat_sheet",
            "content": "Типичные ошибки: " + ", ".join(weaknesses[:3]),
            "url": None
        })
    if topics:
        base.append({
            "title": f"Ресурс по теме: {topics[0]}",
            "type": "link",
            "url": "https://example.com/tutorial",
            "content": None
        })
    return base


def _extract_profile(student_id: str) -> Dict[str, Any]:
    """Берёт профиль из последнего среза куратора (как в examiner.py)."""
    snap = get_last_curator_snapshot(student_id)
    if not snap:
        return {"level": "beginner", "topics": ["базовые понятия"], "weaknesses": []}

    meta = snap.get("meta") or {}
    if isinstance(meta, dict):
        level = meta.get("level", "beginner")
        topics = [str(x) for x in meta.get("topics", []) if str(x).strip()]
        weaknesses = [str(x) for x in meta.get("errors", []) if str(x).strip()]
        return {"level": level, "topics": topics, "weaknesses": weaknesses}

    return {"level": "beginner", "topics": ["базовые понятия"], "weaknesses": []}


def _save_materials_to_db(student_id: str, materials: List[Dict[str, Any]]):
    """Сохраняет материалы в БД (предварительно удаляя старые для этого студента)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM materials WHERE student_id = %s", (student_id,))
            for m in materials:
                cur.execute(
                    """
                    INSERT INTO materials (student_id, title, type, url, content)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (student_id, m["title"], m["type"], m["url"], m["content"]),
                )


def generate_and_save_materials(student_id: str = "default") -> List[Dict[str, Any]]:
    """Генерирует и сохраняет материалы для студента."""
    profile = _extract_profile(student_id)
    materials = _generate_materials_with_llm(
        student_id=student_id,
        level=profile["level"],
        topics=profile["topics"],
        weaknesses=profile["weaknesses"]
    )
    _save_materials_to_db(student_id, materials)
    return materials


def get_materials_for_student(student_id: str = "default") -> List[Dict[str, Any]]:
    """Возвращает материалы студента из БД."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title, type, url, content FROM materials WHERE student_id = %s ORDER BY id",
                    (student_id,)
                )
                rows = cur.fetchall()
                return [
                    {
                        "title": row[0],
                        "type": row[1],
                        "url": row[2],
                        "content": row[3],
                    }
                    for row in rows
                ]
    except Exception as e:
        print(f"[materials_agent] DB error: {e}")
        return []