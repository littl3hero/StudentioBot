from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import List, AsyncGenerator
from app.deps import settings
from openai import AsyncOpenAI
import asyncio
import json

app = FastAPI(title="LLM Backend (Render)")

# CORS: строго на разрешённые фронтенды
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- OpenAI клиент ---
oclient = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = "gpt-4o-mini"
    temperature: float = 0.7

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/v1/chat")  # нестримовая версия (на всякий)
async def chat(req: ChatRequest):
    try:
        resp = await oclient.chat.completions.create(
            model=req.model,
            messages=[m.model_dump() for m in req.messages],
            temperature=req.temperature,
        )
        content = resp.choices[0].message.content
        return {"ok": True, "content": content}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# --- STREAM (SSE) ---
@app.post("/v1/chat/stream")
async def chat_stream(req: ChatRequest):
    async def sse_gen() -> AsyncGenerator[bytes, None]:
        try:
            stream = await oclient.chat.completions.create(
                model=req.model,
                messages=[m.model_dump() for m in req.messages],
                temperature=req.temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    # формат SSE: "data: <payload>\n\n"
                    yield f"data: {json.dumps({'delta': delta})}\n\n".encode("utf-8")
            # маркер завершения
            yield b"data: [DONE]\n\n"
        except Exception as e:
            # передаём ошибку в поток и закрываем
            payload = json.dumps({"error": str(e)})
            yield f"data: {payload}\n\n".encode("utf-8")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream; charset=utf-8",
        # Для некоторых прокси важно отключить буферизацию:
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(sse_gen(), headers=headers, media_type="text/event-stream")
