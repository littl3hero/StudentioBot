# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.deps import settings
from app.routers import legacy_api, agents
from app.agents.materials_agent import init_materials_table  # ← добавь импорт

# ---- Инициализация приложения ----
app = FastAPI(title="Studentio Backend")

# ---- Создаём таблицу при запуске ----
init_materials_table()  # ← добавь эту строку

# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Подключение роутеров ----
app.include_router(legacy_api.router)
app.include_router(agents.router)

@app.get("/health")
def health():
    return {"ok": True}