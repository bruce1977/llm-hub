# LLM Hub - Deployment guide

The gateway is a single codebase with two ways to run it: **Docker (recommended)** — the published image
already contains the console bundle, so one `data` volume is all it needs — and a **local Python** run,
which is the faster loop when you are wiring up upstreams or checking a config change. Both read the same
`data/config.yaml` and `.env`, and clients talk to them identically afterwards (see
[USAGE.md](USAGE.md#api-usage) and [API.md](API.md)). Chinese version: [DEPLOY_CN.md](DEPLOY_CN.md).

| Situation | Where to go |
|---|---|
| Production / long-running | [Quick deployment](#quick-deployment) → `docker run`, config and data live in `data/` |
| Build the image yourself and publish it | [Build / publish scripts (Windows)](#build--publish-scripts-windows) |
| Local debugging without Docker | [Running locally (no Docker)](#running-locally-no-docker) |
| You changed the console frontend | [Console bundle and the image](#console-bundle-and-the-image) (`app/web` is a committed build artifact) |

## Quick deployment

### Prerequisites

- Docker 20.10+

### Step 1: Prepare the configuration

```bash
cp data/config.example.yaml data/config.yaml
cp .env.example .env
```

Edit `.env` to set the API key:

```bash
GATEWAY_API_KEYS=sk-gateway-$(openssl rand -hex 16)
TZ=Asia/Shanghai
```

Edit `data/config.yaml` to add your upstream instances, using `data/config.example.yaml` as the reference.

> The config file is YAML (inside the image `CONFIG_PATH=/data/config.yaml`) and may contain comments. On
> upgrade, if `/data` only holds an old `config.json`, the gateway keeps using it with a warning instead of
> overwriting it with the template; rename the file to `config.yaml` to adopt the new name (JSON is valid
> YAML anyway). Reading the config needs `PyYAML`, which is listed in `requirements.txt`.

### Step 2: Start the service

**Option A: pull from Docker Hub (recommended)**

```bash
docker pull bruce1977/llm-hub:latest

docker run -d --name llm-hub \
  --restart unless-stopped \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  bruce1977/llm-hub:latest
```

**Option B: build from source**

```bash
docker build -t llm-hub:latest .

docker run -d --name llm-hub \
  --restart unless-stopped \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  llm-hub:latest
```

### Step 3: Verify

```bash
curl http://localhost:8888/health
curl http://localhost:8888/v1/models -H "Authorization: Bearer YOUR_API_KEY"
```

Console: open `http://localhost:8888/admin/`, user `admin`, password set through `ADMIN_PASSWORD` in `.env`.
Without that variable the page reports "console not enabled" and every `/v1/management/*` call returns 503;
change the password in `.env` and restart the container to take effect (existing sessions die with it).

---

## Build / publish scripts (Windows)

Two batch files under `scripts\` can be run from cmd or PowerShell, from anywhere inside the repository
(the scripts `cd` to the repo root themselves):

```bat
:: Build only: produces bruce1977/llm-hub:latest and :<version>
scripts\docker-build.bat

:: Build and push to Docker Hub (docker login first)
scripts\docker-publish.bat

:: Override the image name and version
scripts\docker-build.bat registry.example.com/team/llm-hub 1.3.0
```

- The version defaults to `__version__` in `app\main.py` (the value `GET /` reports); the second argument
  overrides it
- Both scripts check that Docker is available first, and exit with code 1 otherwise
- `docker-publish.bat` builds first and only then pushes `latest` and the version tag; a failed push stops
  immediately with a hint to run `docker login`
- Neither script touches git — commit and tag before publishing yourself

## Running locally (no Docker)

### Starting it by hand

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp data/config.example.yaml data/config.yaml
cp .env.example .env                                # fill in GATEWAY_API_KEYS, ADMIN_PASSWORD

cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

The working directory must be `app/` (the same as the image's `WORKDIR /app`), otherwise the
`../data/config.yaml` and `../data/.keys` paths in `.env` do not resolve; `--env-file` points at the `.env`
in the repository root. The console lives at `http://localhost:8000/admin` and requires `app/web` to have
been built — see the next section.

### Launcher scripts (Windows)

To run the gateway straight on the host (debugging upstreams, validating a config), use `start-local.bat` /
`start-local.ps1` from the same directory:

```bat
:: Start after the preflight checks pass (adds --reload)
scripts\start-local.bat

:: Run the preflight checks only, do not start
scripts\start-local.bat -CheckOnly

:: Install dependencies first, then start on another port
scripts\start-local.bat -Install -Port 8888
```

It does three things: locate Python and `data\config.yaml`, inject environment variables from `.env`
(variables already set in the shell win), then run a preflight through the app's **own** modules — missing
dependencies, a config Pydantic rejects, auth enabled without an API key, weak/placeholder keys, a busy port,
an unbuilt `app\web` — and only when all of that passes does it start uvicorn with `--app-dir app` from
`app/`, matching the image's `WORKDIR /app`, so `../data/config.yaml` and `../data/.keys` from `.env` resolve
to the same files locally and in the container.

## Console bundle and the image

The console (`/admin`) frontend is **built ahead of time and committed**: `npm run build` writes into
`app/web`, which is part of the repository, and the `Dockerfile` merely does `COPY app/web /app/web` — the
image carries no node toolchain and never runs `npm` while building. The bundle sits next to the modules, so
the local `app/web` and the image's `/app/web` resolve through the same path (just like `app/static`).
The frontend sources are all TypeScript (`*.ts` modules plus `<script setup lang="ts">` single-file
components), and `npm run build` runs `vue-tsc --noEmit` first, so a type error fails the build; use
`npm run typecheck` for the check alone.

So after touching `web/src` you must regenerate the bundle **before** building the image, and commit
`app/web` along with it:

```bat
cd web
npm install
npm run typecheck
npm run build
cd ..
scripts\docker-build.bat
```

Backend- or config-only changes do not need this step; `npm run dev` (default `http://localhost:5173/admin/`,
proxying `/v1/management` to a gateway on port 8000) is for development only and produces no image content.
If `app/web/index.html` is missing, `/admin` returns 503 with a hint to run `npm run build`; the rest of the
gateway is unaffected.

> **When you overlay the module directory with `-v ./app:/app`** (the hot-patch workflow some docs show):
> the mount replaces the image's whole `/app` with the host's `app/`, so the host copy must have been built
> with `npm run build` too, or `/admin` becomes a 503. The usual `-v ./data:/data` setup is unaffected.

---

## Environment variables

| Variable | Required | Notes |
|------|------|------|
| `GATEWAY_API_KEYS` | ✅* | API keys, comma separated (*either this or the key file below; may be empty when `auth.allow_access_ip_whitelist` is set). **Imported once, on the first start, while the `api_keys` table is empty**; afterwards caller keys live in `llmhub.db` and are minted/revoked on the API keys page in `/admin` — editing this variable has no further effect |
| `GATEWAY_API_KEYS_FILE` | ❌ | Path to a key file. When unset, the gateway reads `.keys` next to `CONFIG_PATH` (e.g. a mounted `/config/.keys`, or `/data/.keys` in the default layout); when set, that path wins. Also first-import only, and keys already in the table are not inserted twice |
| `CONFIG_PATH` | ❌ | Config file path, defaults to `/data/config.yaml` |
| `TZ` | ❌ | Timezone, defaults to `UTC` |
| `SYSTEMONE_SERVER_API_KEY` | ❌ | System One backend key, needed only when `systemone.api_key_env` points at it; required by laya-server, ignored by Ollama 0.40+ backends |
| `OPENCODE_GATEWAY_API_KEY` | ❌ | Web search upstream key, needed only when `websearch.api_key_env` points at it (empty by default = no signature) |
| `ADMIN_PASSWORD` | ❌ | Console login password (the user name is always `admin`). It is a separate credential from caller API keys: it only buys a 12-hour session cookie via `POST /v1/management/auth/login`, and presenting it as `Bearer` / `X-API-Key` is still a 401. Besides that cookie, `/v1/management/*` also accepts a gateway key from the `api_keys` table (so scripts can reuse one), except the three `/api-keys` endpoints — those accept only the session cookie, since otherwise any client could mint itself another key. Unset, `/admin` says the console is not enabled and every `/v1/management/*` call returns 503 |
| `ADMIN_SESSION_TTL` | ❌ | Console session lifetime in seconds, default `43200` (12h), clamped to `300` .. `604800` (5 min .. 7 days). Successful requests past half the TTL renew the session; logout only clears the browser cookie, there is no server-side session table |
| `REQUEST_LOG_DB` | ❌ | SQLite path for request details, defaults to `llmhub.db` next to `CONFIG_PATH` (`/data/llmhub.db` in the container). One file holds three tables: `requests` (details), `bench_results` (speed-test history) and `api_keys` (caller key hashes and masks) |
| `LLMHUB_DB` | ❌ | Database path for the `bench_results` and `api_keys` tables, also `llmhub.db` next to `CONFIG_PATH` by default. Only set it to split those two away from the request log; all three tables share one file by default, so the container needs a single writable `/data` |
| `REQUEST_LOG_RETENTION_DAYS` | ❌ | Retention for request details in days, default `30`; `0` keeps them forever |

> The console writes request details into SQLite (WAL mode) and needs `/data` to be writable. With a
> read-only mount, point `REQUEST_LOG_DB` at a writable path or leave the default location under an empty
> directory instead. Whether rows are written at all is controlled by `request_log.enabled` in
> `config.yaml` (default `true`): the switch is read per request, so flipping it to `false` and saving needs
> no container restart, and since the config mtime is compared at most once per second, writes stop within
> about a second.

---

## Internal vs public authentication

"No key inside the LAN, a valid key from the outside" is a single `auth.trusted_networks` setting in
`config.yaml` - no need to run two separate gateways:

```yaml
auth:
  enabled: false                  # true works too: trusted networks stay key-free
  allow_anonymous_health: true     # / and /health stay anonymous for container probes
  allow_access_ip_whitelist: []
  trusted_networks:
    - 127.0.0.1/32                # in-container HEALTHCHECK
    - 192.168.10.0/24             # LAN range
```

- Inside the trusted networks: every path is key-free (even with an invalid key), `/docs` is visible.
- Outside: every business API needs a valid key (`401` without, `403` with an invalid one), `/docs` 404.
- For a public-only instance use `auth.trusted_networks: []` + `auth.enabled: true` and keep at least
  one key in `api_keys`; otherwise startup warns and all external requests answer 401.
- Never expose the backend Ollama port (11434 etc.) to the internet: that bypasses the gateway and
  the entire auth chain.
- A console session cookie also authenticates business APIs; its lifetime is `ADMIN_SESSION_TTL`.

Behind a reverse proxy uvicorn rewrites the client address from forwarding headers, so the proxy
must **overwrite** rather than append:

```nginx
proxy_set_header X-Forwarded-For $remote_addr;   # not $proxy_add_x_forwarded_for
proxy_set_header X-Forwarded-Proto $scheme;      # lets the session cookie carry Secure
```

Verify:

```bash
curl -o /dev/null -w '%{http_code}\n' http://<lan-ip>:8834/v1/models                                   # 200
curl -o /dev/null -w '%{http_code}\n' http://<public-host>:8834/v1/models                           # 401
curl -H 'X-Forwarded-For: 192.168.10.5' -o /dev/null -w '%{http_code}\n' http://<public-host>:8834/v1/models  # 401
curl -m 3 -o /dev/null -w '%{http_code}\n' http://<public-host>:11434/api/tags                      # unreachable
```

If the third command answers 200, the proxy is trusting a forged `X-Forwarded-For`.

---

## Troubleshooting

### Cannot reach the host's Ollama

```bash
# Make sure Ollama listens on every interface
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# Add the network mapping when starting the container
docker run -d --name llm-hub \
  --add-host=host.docker.internal:host-gateway \
  ...

# Or use the host IP directly (in config.yaml)
"base_url": "http://172.17.0.1:11434"
```

### Health check returns 401

Make sure `auth.allow_anonymous_health` is `true` in `config.yaml`.

### Config changes have no effect

```bash
# Check the file's modification time
stat data/config.yaml

# Some editors do not update mtime — trigger it manually
touch data/config.yaml

# Or restart the container
docker restart llm-hub
```

### The container fails to start

```bash
docker logs llm-hub
```

Common causes: no API key configured, a weak key (needs ≥16 characters), a YAML syntax error.

### Streaming responses cut off

Behind Nginx, disable buffering and raise the timeouts:

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 600s;
```

---

## Operations

```bash
# Logs
docker logs -f llm-hub
docker logs --tail 100 llm-hub

# Restart
docker restart llm-hub

# Update the image
docker pull bruce1977/llm-hub:latest
docker stop llm-hub && docker rm llm-hub
# then re-run the docker run command above

# Back up config
tar -czf llm-hub-backup-$(date +%Y%m%d).tar.gz data/ .env

# Resource limits
docker update --cpus="2.0" --memory=2g llm-hub
```
