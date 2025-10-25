# app.py
import os, json, threading
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests

# ----- Хранилище -----
DATA_PATH = os.path.join(os.path.dirname(__file__), "students.json")
LOCK = threading.Lock()
DEFAULT_LEVEL = "beginner"
ALLOWED_LEVELS = {"beginner", "intermediate", "advanced"}

def load_db() -> Dict[str, Any]:
    if not os.path.exists(DATA_PATH):
        return {}
    with LOCK:
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

def save_db(db: Dict[str, Any]):
    with LOCK:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    if user_id not in db:
        db[user_id] = {"level": DEFAULT_LEVEL, "errors": []}
    return db[user_id]

# ----- LLM клиенты -----
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL")  # напр. "mistral" / "llama3" / "qwen2.5"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

try:
    import openai  # опционально
except Exception:
    openai = None

class LLMClient:
    def generate_tip(self, level: str, errors: List[str]) -> str:
        raise NotImplementedError

class OllamaClient(LLMClient):
    def __init__(self, host: str, model: str):
        self.host, self.model = host.rstrip("/"), model
    def generate_tip(self, level: str, errors: List[str]) -> str:
        sys = ("Ты — Куратор-методист. Кратко и чётко помоги исправить типичные ошибки. "
               "Пиши по-русски, 4–6 маркеров (—), с мини-примерами, подстраивайся под уровень.")
        usr = (f"Уровень: {level}\nОшибки: {', '.join(errors) if errors else 'не указаны'}\n"
               "Сформируй компактную шпаргалку/советы (4–6 строк).")
        payload = {"model": self.model, "messages":[{"role":"system","content":sys},
                                                    {"role":"user","content":usr}],
                   "stream": False, "options":{"temperature":0.6}}
        r = requests.post(f"{self.host}/api/chat", json=payload, timeout=60); r.raise_for_status()
        data = r.json()
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content","").strip() or "— Проверь скобки, знаки и шаги решения."
        msgs = data.get("messages") or []
        return (msgs[-1].get("content","").strip() if msgs else "— Проверь скобки, знаки и шаги решения.")

class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, model: str):
        if openai is None:
            raise RuntimeError("pip install openai")
        openai.api_key, self.model = api_key, model
    def generate_tip(self, level: str, errors: List[str]) -> str:
        sys = ("Ты — Куратор-методист. Кратко и чётко помоги исправить типичные ошибки. "
               "Пиши по-русски, 4–6 маркеров (—), с мини-примерами, подстраивайся под уровень.")
        usr = (f"Уровень: {level}\nОшибки: {', '.join(errors) if errors else 'не указаны'}\n"
               "Сформируй компактную шпаргалку/советы (4–6 строк).")
        resp = openai.chat.completions.create(model=self.model, temperature=0.6,
                    messages=[{"role":"system","content":sys},{"role":"user","content":usr}])
        return resp.choices[0].message.content.strip()

class FallbackClient(LLMClient):
    TEMPL = {
        "beginner": ["— Переписывай условие своими словами.",
                     "— Проверь скобки и знаки на каждом шаге.",
                     "— Подставь простые числа для самопроверки.",
                     "— Сверь порядок величин в ответе.",
                     "— Частые ловушки: забытая скобка, минус, спешка."],
        "intermediate": ["— Перед решением выпиши триггеры ошибок и анти-паттерны.",
                         "— Чек-лист: единицы, границы, частные случаи.",
                         "— Сначала упрощай, потом подставляй.",
                         "— Проверь альтернативным методом или инвариантом.",
                         "— Оформи ключевую лемму."],
        "advanced": ["— Зафиксируй класс задачи и ограничения.",
                     "— Классифицируй ошибки: вычисл./логич./методол.",
                     "— Проверь инварианты на каждом шаге.",
                     "— Дока: крайние случаи + монотонность → общий случай.",
                     "— Придумай контрпример к своему решению."]
    }
    def generate_tip(self, level: str, errors: List[str]) -> str:
        head = "— Ошибки: " + (", ".join(errors[:6]) if errors else "не указаны")
        return head + "\n" + "\n".join(self.TEMPL.get(level, self.TEMPL["beginner"]))

def build_llm() -> LLMClient:
    # 1) Ollama
    if OLLAMA_MODEL:
        try:
            requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
            return OllamaClient(OLLAMA_HOST, OLLAMA_MODEL)
        except Exception:
            pass
    # 2) OpenAI
    if OPENAI_API_KEY:
        try:
            return OpenAIClient(OPENAI_API_KEY, OPENAI_MODEL)
        except Exception:
            pass
    # 3) Fallback
    return FallbackClient()

LLM = build_llm()

# ----- FastAPI -----
app = FastAPI(title="Curator MiniApp")

# раздача статики
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")

# ---- Модели запросов ----
class SetLevelIn(BaseModel):
    user_id: str
    level: str

class AddErrorsIn(BaseModel):
    user_id: str
    errors_text: Optional[str] = ""
    errors_list: Optional[List[str]] = None

class TipIn(BaseModel):
    user_id: str

# ---- Хелперы ----
def parse_errors(text: str) -> List[str]:
    if not text: return []
    raw = [p.strip(" \t\r\n-—*") for p in text.replace(";", ",").split(",")]
    parts: List[str] = []
    for ch in raw:
        parts += [s.strip() for s in ch.splitlines() if s.strip()]
    # dedup + ограничение
    seen, out = set(), []
    for p in parts:
        key = p.lower()
        if p and len(p) <= 200 and key not in seen:
            seen.add(key); out.append(p)
    return out[:20]

# ---- API ----
@app.post("/api/set_level")
def api_set_level(payload: SetLevelIn):
    lvl = payload.level.strip().lower()
    if lvl not in ALLOWED_LEVELS:
        raise HTTPException(400, "bad level")
    db = load_db()
    user = get_user(db, payload.user_id)
    user["level"] = lvl
    save_db(db)
    return {"ok": True, "level": lvl}

@app.post("/api/add_errors")
def api_add_errors(payload: AddErrorsIn):
    db = load_db()
    user = get_user(db, payload.user_id)
    new_list = payload.errors_list or parse_errors(payload.errors_text or "")
    if not new_list:
        raise HTTPException(400, "no errors parsed")
    existing = set(e.lower() for e in user.get("errors", []))
    for e in new_list:
        if e.lower() not in existing:
            user.setdefault("errors", []).append(e)
    save_db(db)
    return {"ok": True, "count_added": len(new_list), "total": len(user["errors"])}

@app.get("/api/profile")
def api_profile(user_id: str):
    db = load_db()
    user = get_user(db, user_id)
    return {"ok": True, "profile": user}

@app.post("/api/tip")
def api_tip(payload: TipIn):
    db = load_db()
    user = get_user(db, payload.user_id)
    advice = LLM.generate_tip(user.get("level", DEFAULT_LEVEL), user.get("errors", []))
    return {"ok": True, "advice": advice}
