# Dockerfile (в КОРНЕ репозитория)
FROM python:3.11-slim

WORKDIR /app

# Системные пакеты по необходимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# 1) Ставим питон-зависимости
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 2) Копируем backend-приложение
COPY backend /app

# Настройки (опц.)
ENV PYTHONUNBUFFERED=1

# Render прокидывает переменную $PORT — слушаем её
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips="*"
