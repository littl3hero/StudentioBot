from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.deps import settings
from app.routers import legacy_api, agents

# ---- Инициализация приложения ----
app = FastAPI(title="Studentio Backend")

# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Подключение роутеров ----
app.include_router(legacy_api.router)   # старое API (student, tests, chat)
app.include_router(agents.router)       # мультиагенты (куратор, экзаменатор, orchestrator)

@app.get("/health")
def health():
    return {"ok": True}