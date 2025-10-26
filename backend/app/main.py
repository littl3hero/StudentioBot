import os, json, asyncio
from typing import List, Optional, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# ---- env ----
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ORIGINS = [o.strip() for o in os.getenv("ALLOW_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# ---- app ----
app = FastAPI(title="Studentio Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- models ----
class StudentProfile(BaseModel):
    name: str
    goals: str
    level: str
    notes: str

class GenerateRequest(BaseModel):
    topic: str
    level: str

class Question(BaseModel):
    id: str
    text: str
    options: List[str]
    answer: Optional[int] = None

class QuizResponse(BaseModel):
    questions: List[Question]

class ChatMsg(BaseModel):
    role: str
    content: str

class ChatStreamRequest(BaseModel):
    messages: List[ChatMsg]
    model: str = OPENAI_MODEL
    temperature: float = 0.7

# ---- in-memory storage (замени на БД позже) ----
_DB_STUDENT: Optional[StudentProfile] = None

# ---- health ----
@app.get("/health")
def health():
    return {"ok": True}

# ---- student endpoints ----
@app.get("/student", response_model=StudentProfile)
def get_student():
    if _DB_STUDENT is None:
        return StudentProfile(name="", goals="", level="beginner", notes="")
    return _DB_STUDENT

@app.post("/student")
def save_student(profile: StudentProfile):
    global _DB_STUDENT
    _DB_STUDENT = profile
    return {"ok": True, "id": "in-memory"}

# ---- tests.generate ----
@app.post("/tests/generate", response_model=QuizResponse)
async def generate_quiz(req: GenerateRequest):
    """
    Демоверсия: если нет OPENAI_API_KEY — вернём фиксированные вопросы.
    С ключом — попросим модель отдать корректный JSON.
    """
    if not OPENAI_API_KEY:
        base = [
            Question(id="q1", text="Определение предела по Коши — это...?",
                     options=["про ε-δ", "про ряды", "про производные", "про интегралы"], answer=0),
            Question(id="q2", text="LIFO-структура данных — это...",
                     options=["Очередь", "Стек", "Дерево", "Граф"], answer=1),
            Question(id="q3", text="Какой порядок у O(log n)?",
                     options=["Линейный", "Константный", "Логарифмический", "Квадратичный"], answer=2),
        ]
        return QuizResponse(questions=base)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
Сделай 3 тестовых вопроса по теме '{req.topic}' для уровня '{req.level}'.
Ответ строго в JSON:
{{
  "questions": [
    {{"id":"q1","text":"...","options":["...","...","...","..."],"answer":0}},
    {{"id":"q2","text":"...","options":["...","...","...","..."],"answer":1}},
    {{"id":"q3","text":"...","options":["...","...","...","..."],"answer":2}}
  ]
}}
Без комментариев и префиксов, только JSON.
"""
    chat = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    text = chat.choices[0].message.content
    try:
        data = json.loads(text)
        return QuizResponse(**data)
    except Exception as e:
        # fallback если модель дала невалидный JSON
        base = [
            Question(id="q1", text="(fallback) Определение предела по Коши — это...?",
                     options=["про ε-δ", "про ряды", "про производные", "про интегралы"], answer=0),
            Question(id="q2", text="(fallback) LIFO-структура данных — это...",
                     options=["Очередь", "Стек", "Дерево", "Граф"], answer=1),
            Question(id="q3", text="(fallback) Какой порядок у O(log n)?",
                     options=["Линейный", "Константный", "Логарифмический", "Квадратичный"], answer=2),
        ]
        return QuizResponse(questions=base)

# ---- chat streaming SSE ----
@app.post("/v1/chat/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    """
    Совместимо с твоим фронтом: fetch('/v1/chat/stream') и парсинг строк "data: {...}\n\n".
    На каждом чанке шлём {"delta": "..."}; в конце — [DONE].
    """

    async def event_gen() -> AsyncGenerator[bytes, None]:
        # heartbeats, чтобы соединение не рубилось
        async def heartbeat():
            yield b": ping\n\n"

        # без ключа — эмуляция стрима (полезно для локалки)
        if not OPENAI_API_KEY:
            text = "Привет! Это демо-стрим без OpenAI ключа."
            for ch in text:
                if await request.is_disconnected():
                    break
                payload = json.dumps({"delta": ch}, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode("utf-8")
                await asyncio.sleep(0.02)
            yield b"data: [DONE]\n\n"
            return

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # подготовим сообщения как есть (role: user/assistant/system)
        msgs = [{"role": m.role, "content": m.content} for m in req.messages]

        try:
            stream = client.chat.completions.create(
                model=req.model or OPENAI_MODEL,
                temperature=req.temperature,
                messages=msgs,
                stream=True,
            )
            # OpenAI SDK даёт генератор событий
            for event in stream:
                if await request.is_disconnected():
                    break
                # в 1.x .delta может сидеть в event.choices[0].delta.content
                choice = event.choices[0]
                delta_piece = ""
                if hasattr(choice, "delta") and getattr(choice.delta, "content", None):
                    delta_piece = choice.delta.content
                elif getattr(choice, "finish_reason", None):
                    # пропускаем
                    delta_piece = ""

                if delta_piece:
                    payload = json.dumps({"delta": delta_piece}, ensure_ascii=False)
                    yield f"data: {payload}\n\n".encode("utf-8")

            # завершение
            yield b"data: [DONE]\n\n"

        except Exception as e:
            err = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {err}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",   # для некоторых прокси
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)
