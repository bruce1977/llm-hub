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
COPY app/logging.json /app/logging.json
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

# 8000 = internal listener (LAN); 8001 = optional public listener. Both ports come
# from config.yaml (server.port / security.public.port) - there are no port
# environment variables. Map them to DIFFERENT host ports and never publish the
# internal socket to the internet. See docs/DEPLOY.md.
EXPOSE 8000 8001

# /health is an anonymous probe on the internal listener. A public-only instance
# (security.public.port == server.port) has no anonymous HTTP path: override this
# healthcheck with a TCP port check in that deployment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# serve.py starts uvicorn with proxy_headers=False. With plain `uvicorn main:app`
# the ProxyHeadersMiddleware rewrites scope["client"] from X-Forwarded-For for
# a same-host reverse proxy (127.0.0.1 is trusted by default), which would make
# IP-based decisions attacker-controlled. See docs/DEPLOY*.md.
#
# serve.py binds server.port and, when configured, security.public.port in ONE
# process. The public listener serves ONLY the method+path pairs listed in
# security.public.path_allowlist (everything else is 404) and every surviving
# request needs a caller key or console session. The management API and /admin
# are always 404 there. 8000 stays the internal zone: APIs key-free when
# security.internal.allow_anonymous=true, /docs and /admin reachable, management
# API still cookie-only.
CMD ["python", "serve.py"]
