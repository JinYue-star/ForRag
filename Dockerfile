# HKU Teacher-student Co-learning (SOLO) Bot — API + static frontend
# Build:  docker compose build
# Run:    docker compose up -d
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System libraries required by onnxruntime/opencv (rapidocr) and PyMuPDF.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

# Install the CPU build of PyTorch first so the default (large CUDA) wheels
# are not pulled in. Remove this line and use a CUDA base image to enable GPU.
RUN pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Persisted data & derived caches live under /app/.data (mounted as a volume).
ENV RAG_DATA_DIR=/app/.data \
    RAG_CACHE_ROOT=/app/.data/vector_cache \
    RAG_CLEAR_CACHE_ON_SHUTDOWN=0 \
    HF_HOME=/app/.hf \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Single worker: Chroma/SQLite state is process-local and not safe across workers.
CMD ["sh", "-c", "uvicorn fastapi_service:app --host 0.0.0.0 --port ${PORT}"]
