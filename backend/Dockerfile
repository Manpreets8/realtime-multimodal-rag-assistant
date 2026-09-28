# Backend image: the API (uvicorn), the ingestion worker and migrations all run from it,
# with different commands (see docker-compose.yml).

# --- build: install dependencies into a virtualenv ------------------------------------------------
FROM python:3.11-slim-bookworm AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install -r requirements.txt

# --- runtime ------------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# libgomp1: OpenMP runtime used by ONNX Runtime (embeddings, reranker, Piper) and CTranslate2 (Whisper).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# An unprivileged user; the data directories are created here so named volumes inherit the owner.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/uploads /models \
    && chown -R app:app /data /models

COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UPLOAD_DIR=/data/uploads \
    MODEL_CACHE_DIR=/models \
    HF_HOME=/models/huggingface

WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

# --proxy-headers: behind the web container's nginx, take the client address from X-Forwarded-For
# (the API port is not published, so only that proxy can reach it; see FORWARDED_ALLOW_IPS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
