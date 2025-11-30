FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.3.1"

RUN pip install --no-cache-dir "sentence-transformers==3.0.1" --no-deps

COPY app /app/app
EXPOSE 10000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000", "--reload"]

