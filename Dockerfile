FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/data/config.json

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

RUN mkdir -p /data

# Runtime configuration directory (config.json); API keys come from the environment
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=1h --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
