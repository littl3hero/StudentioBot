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
    topics = [t for t in topics if t] or ["базовые понятия"]
    weaknesses = [w for w in weaknesses if w]
    qs: List[Dict[str, Any]] = []
    pool = []

    for t in topics[:3]:
        pool.append({
            "text": f"Что из нижнего ближе всего к теме «{t}»?",
            "options": [f"Определение/свойство, относящееся к теме «{t}»",
                        "Несвязанное утверждение",
                        "Пример не из этой области",
                        "Случайное замечание"],
            "answer": 0
        })
    for w in weaknesses[:3]:
        pool.append({
            "text": f"Типичная ошибка: «{w}». Как её избежать?",
            "options": ["Проверять шаги/знаки и промежуточные вычисления",
                        "Игнорировать промежуточные шаги",
                        "Запомнить ответ наизусть",
                        "Не читать условие"],
            "answer": 0
        })
    # добиваем пул нейтральными
    while len(pool) < max(3, count):
        pool.append({
            "text": "Что означает LIFO?",
            "options": ["Последним пришёл — первым вышел",
                        "Первым пришёл — последним вышел",
                        "Случайный порядок",
                        "Нет верного"],
            "answer": 0
        })
    random.shuffle(pool)
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

    Теперь сюда дополнительно прилетает memory_texts — список важных записей
    из памяти студента (его ответы, ошибки, заметки), найденных через эмбеддинги.
    """
    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
    user = {
        "topics": topics or ["базовые понятия"],
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

    prompt = (
        "Сгенерируй тестовые вопросы (множественный выбор, 4 опции) "
        "по темам и слабым местам студента.\n"
        "Учитывай дополнительные записи из памяти студента, если они есть.\n"
        "Ответ строго JSON:\n"
        '{ "questions": ['
        '{"id":"q1","text":"...","options":["...","...","...","..."],"answer":0},'
        '{"id":"q2","text":"...","options":["...","...","...","..."],"answer":1}'
        "]}\n"
        "Без комментариев."
        f"{memory_block}"
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        messages=[
            {"role": "system", "content": "Ты экзаменатор. Дай только валидный JSON по образцу."},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            {"role": "user", "content": prompt},
        ],
    )
    text = resp.choices[0].message.content or "{}"
    data = json.loads(text)
    raw = data.get("questions", [])
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(raw):
        out.append(_sanitize_question(q, i))
    # гарантируем нужное количество
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
