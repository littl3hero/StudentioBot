import os, json, asyncio
from typing import List, Optional, AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.deps import settings

OPENAI_API_KEY = settings.OPENAI_API_KEY
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ORIGINS = [o.strip() for o in os.getenv("ALLOW_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

router = APIRouter()

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

# ---- in-memory storage ----
_DB_STUDENT: Optional[StudentProfile] = None

@router.get("/student", response_model=StudentProfile)
def get_student():
    if _DB_STUDENT is None:
        return StudentProfile(name="", goals="", level="beginner", notes="")
    return _DB_STUDENT

@router.post("/student")
def save_student(profile: StudentProfile):
    global _DB_STUDENT
    _DB_STUDENT = profile
    return {"ok": True, "id": "in-memory"}

# ---- tests.generate ----
@router.post("/tests/generate", response_model=QuizResponse)
async def generate_quiz(req: GenerateRequest):
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

    prompt = f"Сделай 3 тестовых вопроса по теме '{req.topic}' для уровня '{req.level}' в JSON."
    chat = client.chat.completions.create(model=OPENAI_MODEL, temperature=0.3, messages=[{"role": "user", "content": prompt}])
    text = chat.choices[0].message.content
    try:
        return QuizResponse(**json.loads(text))
    except Exception:
        return QuizResponse(questions=[
            Question(id="fallback", text="(fallback) пример", options=["a","b"], answer=0)
        ])

# ---- chat streaming ----
@router.post("/v1/chat/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    async def event_gen() -> AsyncGenerator[bytes, None]:
        if not OPENAI_API_KEY:
            text = "Демо-стрим без ключа OpenAI."
            for ch in text:
                yield f"data: {json.dumps({'delta': ch}, ensure_ascii=False)}\n\n".encode("utf-8")
                await asyncio.sleep(0.02)
            yield b"data: [DONE]\n\n"
            return

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        stream = client.chat.completions.create(
            model=req.model or OPENAI_MODEL,
            temperature=req.temperature,
            messages=[{"role": m.role, "content": m.content} for m in req.messages],
            stream=True,
        )
        for event in stream:
            if await request.is_disconnected():
                break
            delta = event.choices[0].delta.content if hasattr(event.choices[0], "delta") else ""
            if delta:
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
