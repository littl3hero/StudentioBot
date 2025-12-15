# app/agents/examiner.py
from __future__ import annotations
import json
import random
import re
from typing import List, Dict, Any, Optional

from app.deps import settings
from app.memory.vector_store_pg import get_last_curator_snapshot, retrieve_memory
from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError

def _llm() -> Optional[OpenAI]:
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception:
        return None

def _sanitize_question(q: Dict[str, Any], idx: int) -> Dict[str, Any]:
    # приводим к строгому формату
    text = str(q.get("text") or "").strip()
    opts = q.get("options") or []
    if not isinstance(opts, list):
        opts = []
    # гарантируем 4 опции
    opts = [str(x) for x in opts if str(x).strip()][:4]
    while len(opts) < 4:
        opts.append(f"Вариант {len(opts)+1}")
    try:
        ans = int(q.get("answer")) if q.get("answer") is not None else 0
    except Exception:
        ans = 0
    if not (0 <= ans < 4):
        ans = 0
    qid = str(q.get("id") or f"q{idx+1}")
    if not text:
        text = f"(fallback) Вопрос {idx+1}: выбери корректный вариант."
    return {"id": qid, "text": text, "options": opts, "answer": ans}

def _fallback_questions(topics: List[str], weaknesses: List[str], count: int) -> List[Dict[str, Any]]:
    """
    Детерминированный, максимально универсальный фолбэк.
    """
    topics = [str(t).strip() for t in (topics or []) if str(t).strip()]
    weaknesses = [str(w).strip() for w in (weaknesses or []) if str(w).strip()]

    # Выбираем "главный" текст, чтобы хоть что-то подставлять в вопросы
    main_label = None
    if topics:
        main_label = topics[0]
    elif weaknesses:
        main_label = weaknesses[0]
    else:
        main_label = "текущей теме"

    pool: List[Dict[str, Any]] = []

    # 1) Вопросы по темам
    for t in topics[:5]:
        pool.append(
            {
                "text": f"Какое утверждение лучше всего соответствует теме «{t}»?",
                "options": [
                    f"Корректное определение, свойство или факт, относящийся к теме «{t}»",
                    "Утверждение, слабо связанное с темой",
                    "Полностью несвязанное утверждение",
                    "Случайный пример без связи с темой",
                ],
                "answer": 0,
            }
        )

    # 2) Вопросы по слабым местам
    for w in weaknesses[:5]:
        pool.append(
            {
                "text": f"Типичная ошибка: «{w}». Что поможет её избежать?",
                "options": [
                    "Разбирать решение по шагам и осознанно проверять каждый шаг",
                    "Игнорировать детали и полагаться на интуицию",
                    "Запоминать готовые ответы без понимания",
                    "Всегда выбирать самый короткий ответ",
                ],
                "answer": 0,
            }
        )

    # 3) Если тем/ошибок мало — добавим общие «мета»-вопросы
    while len(pool) < max(3, count):
        pool.append(
            {
                "text": f"Что наиболее полезно для закрепления материала по «{main_label}»?",
                "options": [
                    "Решать практические задания и разбирать свои ошибки",
                    "Ничего не повторять и надеяться на удачу",
                    "Ограничиться одним примером и не смотреть остальные",
                    "Сосредоточиться только на запоминании терминов",
                ],
                "answer": 0,
            }
        )

    random.shuffle(pool)

    qs: List[Dict[str, Any]] = []
    for i in range(count):
        qs.append(_sanitize_question(pool[i % len(pool)], i))

    return qs


def _extract_from_snapshot(s: Dict[str, Any]) -> Dict[str, Any]:
    """Пытаемся вытащить topics/weaknesses из meta или из текста."""
    topics: List[str] = []
    weaknesses: List[str] = []
    level = "beginner"

    meta = s.get("meta") or {}
    if isinstance(meta, dict):
        level = meta.get("level", level)
        if isinstance(meta.get("topics"), list):
            topics = [str(x) for x in meta["topics"] if str(x).strip()]
        if isinstance(meta.get("errors"), list):
            weaknesses = [str(x) for x in meta["errors"] if str(x).strip()]

    if not topics or not weaknesses:
        text = s.get("text") or ""
        # примитивный парсинг JSON внутри текста, если он есть
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
    # нормализуем
    topics = topics[:5] if topics else []
    weaknesses = weaknesses[:5] if weaknesses else []
    return {"level": level, "topics": topics, "weaknesses": weaknesses}

def _llm_generate_questions(
    client: OpenAI,
    topics: List[str],
    weaknesses: List[str],
    count: int,
    memory_texts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Генерация вопросов через LLM.

    - Работает для любых тематик (не только математика).
    - Передаём темы, слабые места и память студента.
    - Жёстко просим вернуть ЧИСТЫЙ JSON и аккуратно его парсим.
    """
    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    topics = [t for t in topics if str(t).strip()]
    weaknesses = [w for w in weaknesses if str(w).strip()]

    payload = {
        "topics": topics or ["общие базовые темы"],
        "weaknesses": weaknesses or [],
        "count": count,
        "memory": memory_texts or [],
    }

    memory_block = ""
    if memory_texts:
        joined = "\n".join(f"- {t}" for t in memory_texts)
        memory_block = (
            "\nДополнительный контекст по студенту (его ответы, ошибки, заметки):\n"
            f"{joined}\n"
        )

    system_msg = (
        "Ты экзаменатор.\n"
        "Твоя задача — сгенерировать тестовые вопросы с множественным выбором (4 варианта ответа).\n"
        "Работай с ЛЮБЫМИ темами (школьные, вузовские, программирование, история, что угодно).\n"
        "Каждый вопрос должен явно относиться хотя бы к одной теме или слабому месту студента.\n"
        "Не придумывай вопросы на темы, которых НЕТ в списке.\n"
        "Ответь СТРОГО одним JSON-объектом БЕЗ пояснений, комментариев и текста вокруг.\n"
        "Формат:\n"
        "{\n"
        '  \"questions\": [\n'
        '    {\"id\": \"q1\", \"text\": \"...\","'
        ' \"options\": [\"...\",\"...\",\"...\",\"...\"], \"answer\": 0},\n'
        '    {\"id\": \"q2\", \"text\": \"...\","'
        ' \"options\": [\"...\",\"...\",\"...\",\"...\"], \"answer\": 1}\n'
        "  ]\n"
        "}\n"
        "Где:\n"
        "- answer — это индекс правильного варианта (0, 1, 2 или 3).\n"
        "- Не добавляй никакие другие поля.\n"
    )

    user_msg = (
        "Вот данные о студенте и его контексте:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Сгенерируй ровно {count} вопросов.\n"
        "Все вопросы должны быть содержательно связаны с этими темами/слабыми местами."
        f"{memory_block}"
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )

    raw = resp.choices[0].message.content or "{}"
    cleaned = raw.strip()

    # --- аккуратно убираем ```json ... ``` если модель так ответила ---
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # --- пытаемся вытащить JSON-объект по первым/последним фигурным скобкам ---
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Examiner LLM: no-json-in-output: {cleaned[:200]}")

    json_str = cleaned[start : end + 1]
    data = json.loads(json_str)

    raw_questions = data.get("questions") or []
    out: List[Dict[str, Any]] = []

    for i, q in enumerate(raw_questions):
        if isinstance(q, dict):
            out.append(_sanitize_question(q, i))

    # если LLM дал меньше, чем нужно — добиваем фолбэком
    if len(out) < count:
        out.extend(_fallback_questions(topics, weaknesses, count - len(out)))

    return out[:count]



def generate_exam(count: int = 5, student_id: str = "default") -> Dict[str, Any]:
    """
    Главная функция Экзаменатора.

    1) Берёт последний срез Куратора.
    2) Через эмбеддинги достаёт релевантные записи из student_memory (retrieve_memory).
    3) Пытается сгенерировать вопросы через LLM с учётом памяти.
    4) При любой ошибке — детерминированный fallback (не пустой).
    """
    # --- 1. Срез куратора ---
    snap = get_last_curator_snapshot(student_id)
    topics: List[str] = []
    weaknesses: List[str] = []
    if snap:
        ex = _extract_from_snapshot(snap)
        topics = ex["topics"]
        weaknesses = ex["weaknesses"]

    # Если вообще нет данных — дадим хотя бы базовую тему
    if not topics and not weaknesses:
        topics = ["базовые понятия"]

    # --- 2. Семантический поиск по памяти (через локальные эмбеддинги) ---
    memory_query_parts: List[str] = []
    memory_query_parts.extend(topics)
    memory_query_parts.extend(weaknesses)
    memory_query = " ".join(memory_query_parts).strip() or "общий прогресс и типичные ошибки студента"

    memory_texts: List[str] = []
    try:
        memory_texts = retrieve_memory(memory_query, k=5, student_id=student_id)
    except Exception as e:
        print(f"[examiner] retrieve_memory failed: {e}")
        memory_texts = []

    # --- 3. Пытаемся вызвать LLM ---
    client = _llm()
    if client:
        try:
            qs = _llm_generate_questions(client, topics, weaknesses, count, memory_texts=memory_texts)
            return {"ok": True, "questions": qs, "rubric": "1 балл за верный ответ."}
        except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
            print(f"[examiner] LLM API error: {e}")
        except Exception as e:
            print(f"[examiner] LLM parse error: {e}")

    # --- 4. Fallback всегда непустой и валидный ---
    qs = _fallback_questions(topics, weaknesses, count)
    return {"ok": True, "questions": qs, "rubric": "1 балл за верный ответ. Генерация без LLM."}


# ===== Предподготовленные экзамены (используются оркестратором) =====
_PREPARED_EXAMS: Dict[str, Dict[str, Any]] = {}


def set_prepared_exam(student_id: str, exam_data: Dict[str, Any]) -> None:
    """
    Сохраняем предгенерированный экзамен для студента.
    Оркестратор может вызвать generate_exam заранее, а затем страница /tests заберёт уже готовые вопросы.
    """
    try:
        _PREPARED_EXAMS[student_id] = exam_data
    except Exception as e:
        print(f"[examiner] set_prepared_exam failed: {e}")


def pop_prepared_exam(student_id: str) -> Optional[Dict[str, Any]]:
    """
    Забираем и удаляем предгенерированный экзамен (если он есть).
    Если нет — возвращаем None, и вызывающий код может сгенерировать тест обычным способом.
    """
    try:
        return _PREPARED_EXAMS.pop(student_id, None)
    except Exception as e:
        print(f"[examiner] pop_prepared_exam failed: {e}")
        return None
