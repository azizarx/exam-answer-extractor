# AI Marker API
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    # Persist SQLite under /app/data when DATABASE_URL is unset/overridden
    DATABASE_URL=sqlite:////app/data/exam_db.sqlite

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data /app/storage/uploads /app/storage/results /app/storage/pipeline_runs

EXPOSE 8000

# API only. Frontend is not served from this image.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
