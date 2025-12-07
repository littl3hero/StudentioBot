# app/memory/vector_store_pg.py
from __future__ import annotations

import json
from typing import Optional, List, Dict

import psycopg
from psycopg.rows import dict_row

from app.deps import settings

# ===== ЛОКАЛЬНЫЕ ЭМБЕДДИНГИ =====
try:
    from sentence_transformers import SentenceTransformer

    # одна модель на всё приложение
    _emb_model: Optional[SentenceTransformer] = SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2"
    )
    print("[embeddings] sentence-transformers model loaded: all-MiniLM-L6-v2")
except Exception as e:
    print(f"[embeddings] sentence-transformers unavailable, semantic search disabled: {e}")
    _emb_model = None


def get_conn():
    """
    Подключение к Postgres. autocommit удобен для простых INSERT/SELECT.
    """
    return psycopg.connect(settings.DATABASE_URL, autocommit=True)


def embed_text(text: str) -> Optional[List[float]]:
    """
    Считаем эмбеддинг ЛОКАЛЬНО через sentence-transformers.
    Никакого OpenAI нет.

    Возвращаем:w
      - список float (вектор)
      - либо None, если модель не доступна / текст пустой / ошибка.
    """
    text = (text or "").strip()
    if not text:
        return None
    if _emb_model is None:
        return None
    try:
        vec = _emb_model.encode(text)  # numpy-массив
        return [float(x) for x in vec]
    except Exception as e:
        print(f"[embeddings] local model error: {e}")
        return None


def _to_vector_literal(vec: List[float]) -> str:
    """
    Превращаем список чисел в строку вида: [0.1,0.2,0.3]
    Такой формат понимает pgvector (тип vector).
    """
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def save_memory(student_id: str, text: str, meta: dict):
    """
    Сохраняем запись в student_memory.
    Если embeddings не доступны — пишем NULL в колонку embedding.
    """
    emb = embed_text(text)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if emb is not None:
                emb_lit = _to_vector_literal(emb)
                cur.execute(
                    """
                    INSERT INTO student_memory (student_id, text, meta, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (student_id, text, json.dumps(meta, ensure_ascii=False), emb_lit),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO student_memory (student_id, text, meta, embedding)
                    VALUES (%s, %s, %s, NULL)
                    """,
                    (student_id, text, json.dumps(meta, ensure_ascii=False)),
                )


def retrieve_memory(query: str, k: int = 3, student_id: Optional[str] = None) -> List[str]:
    emb = embed_text(query)
    print(f"[retrieve_memory] START query={query!r}, student_id={student_id!r}, emb_present={emb is not None}")

    with get_conn() as conn:
        # --- 1. Векторный режим (pgvector) ---
        if emb is not None:
            emb_lit = _to_vector_literal(emb)
            print("[retrieve_memory] Using VECTOR search")
            with conn.cursor(row_factory=dict_row) as cur:
                if student_id:
                    print("[retrieve_memory] SQL: vector + student_id")
                    cur.execute(
                        """
                        SELECT text
                        FROM student_memory
                        WHERE student_id = %s
                        ORDER BY embedding <-> %s::vector NULLS LAST
                        LIMIT %s
                        """,
                        (student_id, emb_lit, k),
                    )
                else:
                    print("[retrieve_memory] SQL: vector, all students")
                    cur.execute(
                        """
                        SELECT text
                        FROM student_memory
                        ORDER BY embedding <-> %s::vector NULLS LAST
                        LIMIT %s
                        """,
                        (emb_lit, k),
                    )
                rows = cur.fetchall()
                print(f"[retrieve_memory] VECTOR rows={len(rows)}")
                if rows:
                    texts = [r["text"] for r in rows]
                    print(f"[retrieve_memory] VECTOR result sample: {texts[0][:120]!r}")
                    return texts

        # --- 2. Fallback: триграммы (pg_trgm / similarity) ---
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                if student_id:
                    cur.execute(
                        """
                        SELECT text
                        FROM student_memory
                        WHERE student_id = %s
                        ORDER BY similarity(text, %s) DESC NULLS LAST
                        LIMIT %s
                        """,
                        (student_id, query, k),
                    )
                else:
                    cur.execute(
                        """
                        SELECT text
                        FROM student_memory
                        ORDER BY similarity(text, %s) DESC NULLS LAST
                        LIMIT %s
                        """,
                        (query, k),
                    )
                rows = cur.fetchall()
                if rows:
                    return [r["text"] for r in rows]
        except Exception as e:
            # если нет расширения pg_trgm / similarity → просто логируем
            print(f"[retrieve_memory] trigram fallback unavailable: {e}")

        # --- 3. Last resort: просто последние записи ---
        with conn.cursor(row_factory=dict_row) as cur:
            if student_id:
                cur.execute(
                    """
                    SELECT text
                    FROM student_memory
                    WHERE student_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (student_id, k),
                )
            else:
                cur.execute(
                    """
                    SELECT text
                    FROM student_memory
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (k,),
                )
            rows = cur.fetchall()
            return [r["text"] for r in rows]


# --- helpers to read last curator assessment safely ---


def fetch_recent_memory(student_id: str, kind: Optional[str] = None, limit: int = 1) -> List[Dict]:
    """
    Вытаскиваем последние записи по студенту, опционально фильтруя по meta->>'kind'.
    Используется Куратором/Экзаменатором для чтения последних оценок.
    """
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        if kind:
            cur.execute(
                """
                SELECT id, text, meta
                FROM student_memory
                WHERE student_id = %s AND (meta->>'kind') = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (student_id, kind, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, text, meta
                FROM student_memory
                WHERE student_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (student_id, limit),
            )
        return cur.fetchall()


def get_last_curator_snapshot(student_id: str) -> Optional[Dict]:
    """
    Пытаемся взять последнюю оценку куратора:
    - сначала по kind='curator_assessment'
    - если нет — просто последнюю запись студента.
    """
    rows = fetch_recent_memory(student_id, kind="curator_assessment", limit=1)
    if rows:
        return rows[0]
    rows = fetch_recent_memory(student_id, kind=None, limit=1)
    return rows[0] if rows else None
