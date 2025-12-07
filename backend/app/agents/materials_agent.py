# app/agents/materials_agent.py
import json
import re
from typing import List, Dict, Any, Optional
from app.deps import settings
from urllib.parse import quote_plus
from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError
from app.memory.vector_store_pg import get_conn, get_last_curator_snapshot, retrieve_memory



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

def _build_search_url(platform: str, query: str) -> str:
    """
    Строим нормальные поисковые ссылки, чтобы не было битых урлов от LLM.
    """
    q = quote_plus((query or "").strip() or "обучающий видеоурок")
    platform = (platform or "youtube").lower()

    if platform == "rutube":
        return f"https://rutube.ru/search/?q={q}"
    if platform == "youtube":
        return f"https://www.youtube.com/results?search_query={q}"
    # дефолт — просто гугл
    return f"https://www.google.com/search?q={q}"

def _postprocess_links(raw: List[Dict[str, Any]], topics: List[str]) -> List[Dict[str, Any]]:
    """
    Пробегаемся по материалам типа 'link' и строим нормальные URL по полям
    platform/query, если они есть. Если их нет — используем основную тему.
    """
    main_topic = topics[0] if topics else "общая подготовка"

    for m in raw:
        if (m.get("type") or "").strip() != "link":
            continue

        platform = (m.get("platform") or "youtube").lower()
        query = (m.get("query") or "").strip() or main_topic

        # Если LLM всё-таки вписал url — игнорируем, строим свой
        m["url"] = _build_search_url(platform, query)

    return raw

def _generate_materials_with_llm(
    student_id: str,
    level: str,
    topics: List[str],
    weaknesses: List[str],
    memory_texts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:

    """Генерирует материалы через LLM."""
    client = _llm_client()
    if not client:
        return _fallback_materials(level, topics, weaknesses)

    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
    main_topic = topics[0] if topics else "общая подготовка"

    memory_block = ""
    if memory_texts:
        joined = "\n".join(f"- {t}" for t in memory_texts)
        memory_block = (
            "\nНиже выдержки из диалогов и оценок ученика. "
            "Используй их как контекст (не нужно цитировать дословно, "
            "но опирайся на конкретные вопросы и ошибки):\n"
            f"{joined}\n"
        )

    user_payload = {
        "student_id": student_id,
        "level": level,
        "topics": topics,
        "weaknesses": weaknesses,
        "main_topic": main_topic,
    }

    prompt = f"""
    Ты — учебный ассистент и методист. Тебе в предыдущем сообщении уже передали JSON с информацией о студенте (`student_id`, `level`, `main_topic`, `topics`, `weaknesses`) и, возможно, выдержки из его диалогов и типичных ошибок. На основе этих данных нужно подготовить небольшой набор ПРАКТИЧЕСКИХ материалов, которые реально помогут этому студенту закрыть пробелы и закрепить тему.

Уровень: {level}
Основная тема: {main_topic}
Темы/подтемы: {', '.join(topics) or 'нет явных тем'}
Слабые места: {', '.join(weaknesses) or 'не указаны'}

{memory_block}

Цель:
- Не абстрактная теория, а материалы, которые можно сразу использовать для понимания и решения задач.
- Пояснения простым русским языком, без воды и без фраз вроде «как ИИ я не могу…».

Формат ответа:
- Верни ТОЛЬКО один валидный JSON-объект верхнего уровня вида:

{{
  "materials": [
    {{
      "title": "...",
      "type": "notes | cheat_sheet | link",
      "url": null,
      "content": "...",
      "platform": "youtube | rutube | other",
      "query": "..."
    }}
  ]
}}

Требования к JSON:
1. Снаружи JSON НЕ должно быть никакого текста, комментариев или markdown.
2. В массиве "materials" верни от 4 до 6 объектов.
3. Обязательно должны быть:
   - минимум 1 объект с "type": "notes";
   - минимум 1 объект с "type": "cheat_sheet";
   - 2–3 объекта с "type": "link".

Каждый объект в "materials" обязан содержать поля:
- "title": краткий заголовок (строка);
- "type": "notes" | "cheat_sheet" | "link";
- "url": null (реальный URL будет построен позже, сам его НЕ придумывай);
- "content": строка с текстом материала или null;
- "platform": "youtube" | "rutube" | "other" или null;
- "query": строка с поисковой фразой или null.

Правила по типам:
- Для "type": "notes" и "cheat_sheet":
  - "content" ОБЯЗАТЕЛЕН и не должен быть пустым;
  - "platform" = null;
  - "query" = null;
  - "url" = null.
- Для "type": "link":
  - "content" = null;
  - "platform" и "query" ОБЯЗАТЕЛЬНЫ;
  - "url" всегда = null (не придумывай настоящие ссылки).

Оформление "content" (там, где он не null):
- Это markdown-текст.
- Всегда используй markdown: заголовки, списки, выделение.
- Математические формулы пиши в LaTeX:
  - встроенные: $ ... $
  - блочные: $$ ... $$ 
- Не используй квадратные скобки [ ... ] и круглые ( ... ) как псевдо-формулы. Только LaTeX + markdown.
- Пиши по-русски, короткими абзацами, без лишней воды.

1) Конспект ("type": "notes")
- Сделай 1–2 материала этого типа.
- Цель: дать ученику понятный, структурированный конспект по его теме и слабым местам.

Рекомендуемая структура "content":
- Заголовок уровня # или ## с названием темы.
- Блок "Интуиция" — кратко и простыми словами объясни суть темы.
- Блок "Основные формулы и правила":
- список с LaTeX-формулами.
- Блок "Типовые приёмы / шаги решения" — что обычно делают при решении задач по этой теме.
- 1–2 полностью разобранных примера:
  - чётко сформулированное условие;
  - по шагам, что и почему делаем;
  - короткий итог: что важно запомнить.

Если тема по программированию:
- Добавь 1–2 куска кода в форматированных блоках, например:

```python
# пример функции
def f(x):
    return x**2
Шпаргалка ("type": "cheat_sheet")

Сделай 1–2 материала этого типа.

Цель: компактный листочек "перед задачей", который бьёт именно по слабым местам.

Рекомендуемая структура "content":

Заголовок с темой.

Раздел "Алгоритм решения":

нумерованный список шагов.

Раздел "Типичные ошибки":

список: "Ошибка → как её избежать";

опирайся на слабые места из "weaknesses" и типичные ошибки ученика.

Раздел "Проверь себя":

3–5 мини-вопросов или мини-задач без решения (только условия).

Полезные ссылки ("type": "link")

Сделай 2–3 материала этого типа.

Каждый объект описывает не прямую ссылку, а поисковую фразу для конкретной платформы.

Для каждого "link":

"title" — что это за подборка, например:

"Видеоразборы задач на пределы (ЕГЭ)";

"Практика по динамическому программированию".

"platform" — "youtube", "rutube" или "other".

"query" — конкретная поисковая фраза по теме (не слишком общая), лучше вида:

"предел sin x / x разбор примеров"

"производная физический смысл задачи"
чем "матан" или "математика".

Не придумывай реальные http-ссылки и не вставляй их ни в "url", ни в "content".

Персонализация:

Для уровня "beginner":

очень простые формулировки, больше примеров;

минимум формального языка.

Для "intermediate":

баланс интуиции и аккуратных формул.

Для "advanced":

можно более строгую терминологию и чуть более сложные примеры.

Во всех материалах (конспект, шпаргалка, примеры, вопросы "Проверь себя"):

Используй только те темы, которые реально есть в "topics" и "weaknesses".

Не уходи в абстрактные примеры "ни о чём", если в слабых местах указаны конкретные вещи.

Ещё раз: верни только ОДИН JSON-объект с ключом "materials", без какого-либо текста снаружи.
"""


    try:
        resp = client.chat.completions.create(
        model=model,
        temperature=0.5,
        response_format={"type": "json_object"},  # <<< ВАЖНО
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты генератор учебных материалов. "
                    "Всегда отвечай ТОЛЬКО валидным JSON по заданной схеме. "
                    "Не добавляй никакого текста до или после JSON."
                ),
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            {"role": "user", "content": prompt},
        ],
    )

        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)          # теперь без try/except/регексов
        raw = data.get("materials", [])
        raw = _postprocess_links(raw, topics)
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
    """Фолбэк-материалы без LLM, но всё равно полезные."""
    main_topic = topics[0] if topics else "общая подготовка"
    weaknesses = weaknesses or []

    notes_content = (
        f"Конспект по теме: {main_topic}\n\n"
        "1. Цель\n"
        f"- Разобраться в основных идеях темы «{main_topic}» и научиться применять их в задачах.\n\n"
        "2. Ключевые идеи\n"
        "- Выпиши себе 3–5 ключевых фактов или правил по теме.\n"
        "- Попробуй объяснить тему своими словами вслух.\n\n"
        "3. Примеры\n"
        "- Найди 2 простых и 2 средних примера по теме.\n"
        "- Реши их письменно, комментируя каждый шаг.\n\n"
        "4. Мини-практика\n"
        "- Составь 3 мини-вопроса по теме и попробуй ответить без подсказки.\n"
    )

    if weaknesses:
        ws = "\n".join(f"- {w}" for w in weaknesses[:5])
        notes_content += "\n5. На что обратить внимание (твои слабые места):\n" + ws + "\n"

    cheat_content_lines = [
        f"Шпаргалка по теме: {main_topic}",
        "",
        "Типичные ошибки и как их избегать:",
    ]
    if weaknesses:
        for w in weaknesses[:5]:
            cheat_content_lines.append(f"- Ошибка: {w}")
            cheat_content_lines.append("  → Что делать: разложи задачу на шаги и проверь каждый шаг отдельно.")
    else:
        cheat_content_lines.append("- Отсутствуют явно выделенные ошибки — делай упор на понимание примеров и проверку каждого шага.")
    cheat_content_lines.append("")
    cheat_content_lines.append("Мини-чек-лист перед задачами:")
    cheat_content_lines.extend(
        [
            "- Понимаю ли я, что от меня хотят в условии?",
            "- Могу ли я переформулировать задачу простыми словами?",
            "- Знаю ли я нужные формулы/правила?",
            "- Проверил ли я знаки, границы, единицы измерения?",
        ]
    )

    cheat_content = "\n".join(cheat_content_lines)

    # ссылки — просто поисковые страницы по теме
    yt_url = _build_search_url("youtube", main_topic)
    rt_url = _build_search_url("rutube", main_topic)

    base = [
        {
            "title": f"Конспект по теме: {main_topic}",
            "type": "notes",
            "content": notes_content,
            "url": None,
        },
        {
            "title": "Шпаргалка: типичные ошибки и чек-лист",
            "type": "cheat_sheet",
            "content": cheat_content,
            "url": None,
        },
        {
            "title": f"Видео по теме «{main_topic}» (YouTube)",
            "type": "link",
            "content": None,
            "url": yt_url,
        },
        {
            "title": f"Видео по теме «{main_topic}» (RuTube)",
            "type": "link",
            "content": None,
            "url": rt_url,
        },
    ]

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
    """Генерирует и сохраняет материалы для студента с учётом его памяти (чатов/оценок)."""
    profile = _extract_profile(student_id)
    topics = profile["topics"]
    weaknesses = profile["weaknesses"]

    # Собираем запрос для памяти: тема + слабые места
    query_parts: list[str] = []
    query_parts.extend(topics)
    query_parts.extend(weaknesses)
    memory_query = " ".join(query_parts).strip() or "типичные ошибки и вопросы ученика"

    try:
        memory_texts = retrieve_memory(memory_query, k=5, student_id=student_id)
    except Exception as e:
        print(f"[materials_agent] retrieve_memory failed: {e}")
        memory_texts = []

    materials = _generate_materials_with_llm(
        student_id=student_id,
        level=profile["level"],
        topics=topics,
        weaknesses=weaknesses,
        memory_texts=memory_texts,   # <<< НОВОЕ
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