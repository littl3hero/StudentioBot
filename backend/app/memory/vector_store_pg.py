# app/memory/vector_store_pg.py
from __future__ import annotations
import json
from typing import Optional, List

import psycopg
from psycopg.rows import dict_row

from openai import OpenAI
from openai import RateLimitError, APIConnectionError, APIStatusError, AuthenticationError

from app.deps import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def get_conn():
    # autocommit удобен для простых INSERT/SELECT
    return psycopg.connect(settings.DATABASE_URL, autocommit=True)


def embed_text(text: str) -> Optional[List[float]]:
    """Вернуть вектор, либо None если embeddings недоступны (квота/ключ/сеть)."""
    if not settings.OPENAI_API_KEY:
        return None
    try:
        model = getattr(settings, "EMBEDDINGS_MODEL", None) or "text-embedding-3-small"
        resp = client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
        print(f"[embeddings] disabled due to API error: {e}")
        return None
    except Exception as e:
        print(f"[embeddings] unexpected error: {e}")
        return None


def _to_vector_literal(vec: List[float]) -> str:
    # pgvector приемлет строку вида: [0.1,0.2,0.3]
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def save_memory(student_id: str, text: str, meta: dict):
    """Сохраняем запись. Если embeddings не доступны — пишем NULL."""
    emb = embed_text(text)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if emb:
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
    """
    1) Если есть embeddings — используем векторный поиск.
    2) Иначе пробуем триграммы (pg_trgm): ORDER BY similarity(text, %s) DESC.
    3) Если и это недоступно — просто берём последние записи.
    """
    emb = embed_text(query)
    with get_conn() as conn:
        # Векторный режим
        if emb:
            emb_lit = _to_vector_literal(emb)
            with conn.cursor(row_factory=dict_row) as cur:
                if student_id:
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
                return [r["text"] for r in rows]

        # Fallback: триграммы, если расширение pg_trgm доступно
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
            print(f"[retrieve_memory] trigram fallback unavailable: {e}")

        # Last resort: просто несколько последних (без сортировки по похожести)
        with conn.cursor(row_factory=dict_row) as cur:
            if student_id:
                cur.execute(
                    "SELECT text FROM student_memory WHERE student_id = %s ORDER BY id DESC LIMIT %s",
                    (student_id, k),
                )
            else:
                cur.execute("SELECT text FROM student_memory ORDER BY id DESC LIMIT %s", (k,))
            rows = cur.fetchall()
            return [r["text"] for r in rows]
# --- helpers to read last curator assessment safely ---
from typing import Optional, List, Dict
from psycopg.rows import dict_row

def fetch_recent_memory(student_id: str, kind: Optional[str] = None, limit: int = 1) -> List[Dict]:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        if kind:
            cur.execute(
                """SELECT id, text, meta
                   FROM student_memory
                   WHERE student_id=%s AND (meta->>'kind')=%s
                   ORDER BY id DESC
                   LIMIT %s""",
                (student_id, kind, limit),
            )
        else:
            cur.execute(
                """SELECT id, text, meta
                   FROM student_memory
                   WHERE student_id=%s
                   ORDER BY id DESC
                   LIMIT %s""",
                (student_id, limit),
            )
        return cur.fetchall()

def get_last_curator_snapshot(student_id: str) -> Optional[Dict]:
    # сначала пробуем по kind='curator_assessment'
    rows = fetch_recent_memory(student_id, kind="curator_assessment", limit=1)
    if rows:
        return rows[0]
    # иначе — просто последняя запись
    rows = fetch_recent_memory(student_id, kind=None, limit=1)
    return rows[0] if rows else None