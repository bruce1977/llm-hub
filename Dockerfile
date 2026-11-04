FROM python:3.12-slim

# Metadata only: scripts/docker-build.bat passes __version__ from app/main.py.
ARG VERSION=dev
LABEL org.opencontainers.image.title="llm-hub" \
      org.opencontainers.image.description="Aggregate Ollama / LM Studio / OpenAI-compatible backends behind one API" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/data/config.yaml

# /app holds every *.py module at the top level, so the local source directory
# can be mounted straight onto it for instant patches.
WORKDIR /app

# curl is only needed by the container health check
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first: this layer stays cached when only source changes
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app/*.py /app/
COPY app/static /app/static

# Web console: built beforehand with `npm run build` in web/, which writes app/web
# (committed as plain static files), so the image needs no node toolchain.
# It sits next to the modules exactly like app/static and is served at /admin.
COPY app/web /app/web

RUN mkdir -p /data

# Runtime state lives here: config.yaml, plus llmhub.db (request log and speed-test
# history, WAL mode). The container stays root on purpose - a bind mount normally
# arrives owned by the host user, and a non-root uid could not create the database.
VOLUME ["/data"]

EXPOSE 8000

# /health skips auth (auth.allow_anonymous_health), so this works without a key.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
