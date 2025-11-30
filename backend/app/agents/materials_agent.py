# app/agents/materials_agent.py
import json
import re
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
Ты — учебный ассистент. Создай 4–6 РАЗНЫХ материалов под студента.
Дано:
- Уровень: {level}
- Темы: {', '.join(topics) or 'базовые понятия'}
- Проблемы: {', '.join(weaknesses) or 'нет данных'}

Сделай минимум:
- 1 подробный конспект теории;
- 1 шпаргалку по типичным ошибкам;
- 1 подборку задач или полезных ссылок.

Ответь строго в формате JSON **без пояснений и текста вокруг**:

{{
  "materials": [
    {{
      "title": "краткий заголовок",
      "type": "notes | cheat_sheet | link",
      "url": "ссылка или null",
      "content": "текст конспекта/шпаргалки или null"
    }}
  ]
}}
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

def _material_key(m: Dict[str, Any]) -> str:
    """Уникальный ключ материала для отсечения дублей."""
    title = (m.get("title") or "").strip()
    typ = (m.get("type") or "").strip()
    url = (m.get("url") or "" or "").strip()
    content = (m.get("content") or "").strip()
    return f"{title}||{typ}||{url}||{content}"

def _sanitize_materials(raw: List[Dict]) -> List[Dict[str, Any]]:
    out = []
    seen: set[str] = set()

    for m in raw:
        typ = m.get("type", "notes")
        if typ not in {"link", "notes", "cheat_sheet"}:
            typ = "notes"

        normalized = {
            "title": str(m.get("title") or "Без названия")[:100],
            "type": typ,
            "url": str(m.get("url")) if m.get("url") else None,
            "content": str(m.get("content")) if m.get("content") else None,
        }

        key = _material_key(normalized)
        if key in seen:
            continue  # дубликат в одной генерации
        seen.add(key)
        out.append(normalized)

    return out


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
    """
    Берём профиль из последнего среза куратора:
    - сначала пробуем meta;
    - если там нет topics/weaknesses, вытаскиваем их из JSON profile в поле text.
    Логика такая же, как в examiner._extract_from_snapshot.
    """
    snap = get_last_curator_snapshot(student_id)
    if not snap:
        return {"level": "beginner", "topics": ["базовые понятия"], "weaknesses": []}

    topics: List[str] = []
    weaknesses: List[str] = []
    level = "beginner"

    meta = snap.get("meta") or {}
    if isinstance(meta, dict):
        level = meta.get("level", level)
        if isinstance(meta.get("topics"), list):
            topics = [str(x) for x in meta["topics"] if str(x).strip()]
        if isinstance(meta.get("errors"), list):
            weaknesses = [str(x) for x in meta["errors"] if str(x).strip()]

    # если из meta не достали — парсим JSON profile внутри text
    if not topics or not weaknesses:
        text = snap.get("text") or ""
        m = re.search(r'profile:\s*(\{.*\})', text, re.IGNORECASE | re.DOTALL)
        if m:
            try:
                prof = json.loads(m.group(1))
                if not topics:
                    topics = [str(x) for x in prof.get("topics", []) if str(x).strip()]
                if not weaknesses:
                    weaknesses = [str(x) for x in prof.get("weaknesses", []) if str(x).strip()]
                if "level" in prof:
                    level = prof.get("level") or level
            except Exception:
                pass

    topics = topics[:5] if topics else ["базовые понятия"]
    weaknesses = weaknesses[:5] if weaknesses else []
    return {"level": level, "topics": topics, "weaknesses": weaknesses}



def _save_materials_to_db(student_id: str, materials: List[Dict[str, Any]]):
    """
    Сохраняет материалы в БД, копя историю, но не дублируя уже существующие
    (по title+type+url+content).
    """
    if not materials:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) берём уже существующие материалы студента
            cur.execute(
                """
                SELECT title, type, url, content
                FROM materials
                WHERE student_id = %s
                """,
                (student_id,),
            )
            existing_keys: set[str] = set()
            for title, typ, url, content in cur.fetchall():
                existing_keys.add(
                    _material_key(
                        {
                            "title": title,
                            "type": typ,
                            "url": url,
                            "content": content,
                        }
                    )
                )

            # 2) вставляем только новые
            for m in materials:
                key = _material_key(m)
                if key in existing_keys:
                    continue  # уже есть — пропускаем
                existing_keys.add(key)
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