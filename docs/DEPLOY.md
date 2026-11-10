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

Edit `.env` to set the console password:

```bash
ADMIN_PASSWORD=$(openssl rand -hex 24)
TZ=Asia/Shanghai
```

Caller API keys are not in this file: sign in at `/admin` and mint them on the API keys page.

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
cp .env.example .env                                # fill in ADMIN_PASSWORD

cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env --no-proxy-headers
# For a non-reload start or a public listener use the hardened entrypoint instead:
# python serve.py     (proxy_headers=False; binds server.port and security.public.port from config.yaml)
```

`--no-proxy-headers` is required even for a local uvicorn start: without it
`ProxyHeadersMiddleware` rewrites `scope["client"]` from a caller-supplied `X-Forwarded-For`
when the peer is `127.0.0.1`, which defeats the IP whitelist. `serve.py` sets the equivalent
`proxy_headers=False` in code.

The working directory must be `app/` (the same as the image's `WORKDIR /app`), otherwise the
`../data/config.yaml` path in `.env` does not resolve; `--env-file` points at the `.env`
in the repository root. The console lives at `http://localhost:8000/admin` and requires `app/web` to have
been built — see the next section.

### Launcher scripts (Windows)

To run the gateway straight on the host (debugging upstreams, validating a config), use `start-local.bat` /
`start-local.ps1` from the same directory:

```bat
:: Single internal listener (server.port from config.yaml), uvicorn + auto-reload
:: (passes --no-proxy-headers). Change the port in config.yaml, not on the command line.
scripts\start-local.bat

:: Run the preflight checks only, do not start
scripts\start-local.bat -CheckOnly

:: Install dependencies first, then start
scripts\start-local.bat -Install

:: Two-zone (internal 8000 + public 8001): first set in data\config.yaml
::   security:
::     public:
::       port: 8001
::       path_allowlist: { GET: ['^/v1/models$'], POST: ['^/v1/chat/completions$'] }
:: a distinct public port auto-selects the hardened serve.py entrypoint.
scripts\start-local.bat

:: Validate the exact production startup path locally (serve.py, no auto-reload)
scripts\start-local.bat -NoReload
```

`start-local.ps1` takes only `-Config`, `-EnvFile`, `-Python`, `-NoReload`, `-Install` and
`-CheckOnly` — there is no `-Port`, `-Bind`, `-LogLevel`, `-PublicPorts` or `-PublicAdmin` flag.
Bind host, port, log level and zone policy all come from `server.host` / `server.port` /
`server.log_level` / `security.public.*` / `security.internal.*` in `data\config.yaml`.

It locates Python and `data\config.yaml`, injects environment variables from `.env` (variables
already set in the shell win), then runs a preflight through the app's **own** modules — missing
dependencies, a config Pydantic rejects, weak/placeholder keys, the fail-closed key guard (zero
keys and no anonymous internal zone), a busy **internal or public** port, and an unbuilt `app\web`.
It then picks the entrypoint:

- **Default (single internal listener)** — uvicorn with `--app-dir app` from `app/` (the image's
  `WORKDIR /app`) plus `--no-proxy-headers` and auto-reload, so `../data/config.yaml`
  resolves exactly as in the container.
- **A distinct public port in config** (`security.public.port` differs from `server.port`) **or
  `-NoReload`** — `python serve.py`, which binds the internal and public listeners in one process
  and applies the public-zone path policy. No auto-reload; restart after editing `app/*.py`.

Use `-CheckOnly` to print the resolved zone layout (`internal=…`, `public=…`, the configured
method+path rule count and the chosen entrypoint) without binding anything. Any legacy listener
variables still in the environment are reported as warnings (explicit config wins) and should be
removed from `.env`.

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

`.env.example` names the three variables this service understands: `CONFIG_PATH` (commented, with
its default), `ADMIN_PASSWORD` and `SYSTEMONE_SERVER_API_KEY` (commented, optional). Ports, zones,
the allowlist, upstreams and timeouts all live in `data/config.yaml`. **Caller API keys are not an
environment concern any more** — they are minted at `/admin` -> API keys and stored as hashes in
`llmhub.db`, so no variable, key file or config node can seed them.

| Variable | Required | Notes |
|------|------|------|
| `CONFIG_PATH` | ❌ | Config file path, defaults to `/data/config.yaml` |
| `TZ` | ❌ | Timezone, defaults to `UTC` |
| `SYSTEMONE_SERVER_API_KEY` | ❌ | System One backend key, needed only when `systemone.api_key_env` points at it; required by laya-server, ignored by Ollama 0.40+ backends |
| `OPENCODE_GATEWAY_API_KEY` | ❌ | Web search upstream key, needed only when `websearch.api_key_env` points at it (empty by default = no signature) |
| `ADMIN_PASSWORD` | ❌ | Console login password (the user name is always `admin`). It is a separate credential from caller API keys: it only buys a 12-hour session cookie via `POST /v1/management/auth/login`, and presenting it as `Bearer` / `X-API-Key` is still a 401. Every `/v1/management/*` route accepts **only** that signed session cookie — a gateway API key is handed to every client and answers 401 here, so scripts must log in once and keep the cookie jar. Unset, `/admin` says the console is not enabled and every `/v1/management/*` call returns 503 |
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

> **Legacy listener variables — remove them.** `LLMHUB_HOST`, `LLMHUB_PORT`, `LLMHUB_LOG_LEVEL`,
> `LLMHUB_PUBLIC_PORTS` (singular alias `LLMHUB_PUBLIC_PORT`), `LLMHUB_PUBLIC_ADMIN` and
> `GATEWAY_AUTH_DISABLED` are **no longer active controls** — they are not listed above because
> ports, bind address, log level and zone policy now come only from `config.yaml`
> (`server.host` / `server.port` / `server.log_level`, `security.public.port`,
> `security.internal.allow_anonymous`). On startup the app still reads any leftover value **once**
> as a migration hint (e.g. `LLMHUB_PUBLIC_PORTS` → `security.public.port`,
> `GATEWAY_AUTH_DISABLED` → `security.internal.allow_anonymous`), warns, and lets the explicit
> config win. Delete these lines from `.env`, your shell and any compose/`docker run -e` flags;
> a stale one is silently ignored after migration and only adds noise to the preflight.

---

## Internal vs public authentication

### Trust anchors

A request is `internal` or `public` by the **local listening port the connection arrived on**
(`scope["server"]`, assigned by the kernel) — never by a client IP, `X-Forwarded-For`, an API key
or any request header. `serve.py` sets `proxy_headers=False` so uvicorn cannot rewrite
`scope["client"]` from a forged XFF, and `auth.peer_ip()` rejects any scope carrying the port-0
proxy-rewrite fingerprint. The application parses XFF itself — **rightmost untrusted hop** — only
for TCP peers declared in `security.trusted_proxies`, and uses it solely for IP fencing,
rate-limit buckets and audit rows; it never changes the zone. Mapping two Docker host ports to one
container port does **not** create two zones — the container sees a single socket.

| Anchor | Configured by | Forgeable by a caller |
|---|---|---|
| Internal listener (kernel-assigned socket) | `server.port` | No |
| Public listener (kernel-assigned socket) | `security.public.port` | No |
| TCP peer of a declared reverse proxy | `security.trusted_proxies` (XFF for IP fencing / rate limit / audit only) | No |

`security.public.port` selects the layout:

- `null` — no public listener (internal-only instance).
- different from `server.port` (e.g. `8001`) — internal plus public in **one process**.
- equal to `server.port` (e.g. `8000`) — **public-only** single socket: no anonymous HTTP path
  at all (not even `/health`), so use a TCP port check for probes.

Hard rules:

- With two listeners (`security.public.port: 8001`), the **internal** port (`server.port`, 8000)
  is trusted by its socket alone: with `security.internal.allow_anonymous: true` normal APIs are
  key-free, `/docs` and `/admin` are reachable (docs per `security.internal.expose_docs`), and the
  management API still needs its console cookie. No CIDR list and no `auth` node is involved.
  Publish 8000 on the LAN only.
- A request landing on the **public** port (8001) **always** needs a valid caller key (or a signed
  console session cookie). `security.public.path_allowlist` is the complete list of existing
  method+path pairs (`re.fullmatch`); management, docs, `/`, `/probe` and `/health` are red lines —
  see [Public zone](#public-zone-securitypublicport) below. `/admin` is **404** — the public zone
  has no admin-shell toggle.
- A request carrying `X-Forwarded-*` / `X-Real-IP` is used only for IP fencing / rate limiting /
  audit when its real TCP peer is a declared `security.trusted_proxies` member, via the rightmost
  untrusted XFF hop; it never changes the zone. This stops a common `^127\.0\.0\.1$` fence from
  being satisfied by a forged leftmost hop.
- Zero API keys with no anonymous internal zone fails closed: the gateway **refuses to start** and
  logs an ERROR. Anonymous now means `security.internal.allow_anonymous: true` on a real internal
  socket only — never a global mode and never the public zone.
- On the internal socket `/` and `/health` are **always** anonymous (liveness/info probes),
  independent of `allow_anonymous`. On the public socket they return **404** unless explicitly
  listed in `security.public.path_allowlist`, and even then still need a key: **there is no
  anonymous `/health` on the internet-facing port**. Point the container HEALTHCHECK and load
  balancers at the internal port (`server.port`). A public-only instance
  (`security.public.port == server.port`) has no anonymous `/health` at all — replace the image's
  HTTP HEALTHCHECK with a TCP port check, or add a separate internal listener.
- Never expose the backend Ollama port (11434 etc.) to the internet: that bypasses the whole chain.

### Internal zone (`server.port`)

The trusted LAN socket. `/` and `/health` are **always anonymous** (liveness/info probes),
regardless of `security.internal.allow_anonymous`. That flag governs every other business API:
`true` makes them key-free on this port only; `false` requires a caller key or console session.
`/docs`, `/openapi.json` and the Swagger assets are served only when
`security.internal.expose_docs: true`, and the anonymous `/` reveals the exact build version only
when `security.internal.expose_version: true`. `/v1/management/*` always requires the console
session cookie — a gateway API key returns 401 here, in either zone.

The key guard lives here. Caller keys are minted only in the console, so an **empty** `api_keys`
table is a normal first-run state: every request that needs a key answers 401 until you sign in at
`/admin` and create one. Startup refuses to continue only when there is no way out — an empty table,
no `ADMIN_PASSWORD` (so the console cannot mint anything) and no anonymous internal zone.

### Public zone (`security.public.port`)

The internet-facing socket. **Every request requires a valid caller key or a signed console
session cookie** — there is no `allow_anonymous` toggle, and the public zone can never be
anonymous. The console session cookie (`llmhub_admin`) is accepted in both zones; a gateway API
key still cannot open `/v1/management/*`.

Only the **method + regex** pairs listed in `security.public.path_allowlist` exist. Keys are HTTP
methods, values are full-match regexes (`re.fullmatch`, not prefixes — `^/v1/models$` does not
match `/v1/models/`; a trailing slash is a different path). Method matters, and any method+path
not listed returns **404 before routing**, with or without a key (never 403). An invalid regex is
dropped with a warning (fail closed); an invalid method key is a config `ValidationError`. A wrong
method on an allowed path is also 404 here. There is no global method allowlist any more — the
mapping *is* the public surface, so adding an endpoint is an explicit config edit.

Red lines on the public socket — management, docs, `/`, `/probe` and `/health` are all unreachable:

- `/v1/management/*`, `/admin` and `/admin/*`, plus any path with a `..` segment or a NUL byte are
  **hard 404 in code even if you list them** in `security.public.path_allowlist`. In particular
  `/admin` is 404 — there is no admin-shell switch and no static console on the public port.
- `/docs`, `/openapi.json` and `/static/swagger/*` are internal-only by code.
- `/`, `/probe` and `/health` are not in the default allowlist; even if added, they still need a
  key — there is **no anonymous HTTP path on the public zone**. Use a TCP port check for a
  public-only instance.

### Choice A - one container port, internal-only or public-only

One listener in the container means **one zone**, because the container sees only ONE socket and
`scope["server"]` is identical for every host port mapped to it. Two **different host ports pointing
at the same container port do NOT create two zones** — no reverse proxy, IP whitelist or header can
split that socket. Set the whole instance to one of the two single-socket layouts in `config.yaml`:

1. **Internal-only** — `security.public.port: null`. The sole `server.port` socket is trusted LAN;
   `security.internal.allow_anonymous` decides whether business APIs need a key. Publish it on the
   LAN (or behind a VPN/SSH tunnel) only.
2. **Public-only** — `security.public.port` **equals** `server.port` (e.g. both `8000`). The sole
   socket is untrusted: every request needs a key or session, only `security.public.path_allowlist`
   method+path pairs exist, and **no HTTP path is anonymous** (not even `/health`). Override the
   image's HTTP HEALTHCHECK with a TCP port check.

```yaml
server:
  host: 0.0.0.0
  port: 8000
  log_level: info
security:
  internal:
    allow_anonymous: false        # internal-only: true for a LAN-only key-free instance
    expose_version: false
    expose_docs: true
  public:
    port: null                    # internal-only; set to 8000 (== server.port) for public-only
    path_allowlist:              # ignored while port is null; required when public-only
      GET:
      - ^/v1/models$
      POST:
      - ^/v1/chat/completions$
      - ^/v1/messages$
      - ^/api/generate$
      - ^/api/chat$
```

If you need a trusted LAN zone *and* an internet-facing zone with different policies, use Choice B.
Do not try to express that split with `security.trusted_proxies` — it is not a zone anchor.

### Choice B - two container listeners (strongest, recommended)

`serve.py` binds `server.port` and `security.public.port` in a **single process**, so the in-memory
API-key cache stays shared (a revoked key stops working on both ports immediately), as do
rate-limit buckets and the console sign-in lockout. **Ports come from the mounted `config.yaml`,
not from environment variables** — the `data/` volume holds both `config.yaml` and `llmhub.db`:

```yaml
# data/config.yaml (mounted at /data/config.yaml)
server:
  host: 0.0.0.0
  port: 8000
  log_level: info
security:
  internal:
    allow_anonymous: true
    expose_version: false
    expose_docs: true
  public:
    port: 8001                    # second socket: internal 8000 + public 8001 in ONE process
    path_allowlist:              # the COMPLETE method+path surface of the public socket
      GET:
      - ^/v1/models$
      POST:
      - ^/v1/chat/completions$
      - ^/v1/messages$
      - ^/api/generate$
      - ^/api/chat$
```

```bash
docker run -d --name llm-hub \
  -e ADMIN_PASSWORD='<32+ random characters>' \
  -p 192.168.10.5:8834:8000 \
  -p 127.0.0.1:8835:8001 \
  -v /path/to/data:/data \
  llm-hub:latest
```

`192.168.10.5:8834 -> 8000` is the **internal** listener, `127.0.0.1:8835 -> 8001` is the
**public** listener reached only through nginx. Map the two container ports to **DIFFERENT host
ports**: two host ports pointing at one container socket do NOT create two zones. `security.public.port: 8001`
is what makes `serve.py` open both listeners. If it equals `server.port` (e.g. `8000`), only one
socket is opened and the whole instance is public-only (no anonymous HTTP path — use a TCP probe).
Ports and zone policy have no environment-variable controls; the mounted `config.yaml` is the only
source (see the legacy note above for the removed variables).

| Path | 8000 internal | 8001 public |
|---|---|---|
| Normal APIs (`/v1/chat/completions`, `/v1/messages`, `/api/generate`, …) | Key-free with `security.internal.allow_anonymous: true` | **401** without a valid key or session; a valid key works |
| Paths not in `security.public.path_allowlist` | Normal routing | **404** (with or without a key) |
| `/v1/management/*` | Cookie only (401 with a gateway key) | **404** (hard red line) |
| `/docs`, `/openapi.json`, `/static/swagger/*` | Reachable when `security.internal.expose_docs: true` | **404** |
| `/v1/models`, `/api/tags`, `/api/ps`, `/probe`, `/api/version`, `/` | Reachable | **404** unless explicitly listed (and then still key-gated) |
| `/admin`, `/admin/*` | Static console + management routes | **404** (hard red line; no admin-shell switch) |
| `/health` | Anonymous (point probes here) | **404** unless listed; even then still needs a key |

The public listener is **not a second proxy process**: it is the same in-process app with a policy
envelope applied before routing, so a key revoked in the console stops working on both ports at once
and a streaming response is never re-framed by a second HTTP hop. Hard red lines the config cannot
open: `/v1/management/*`, `/admin*`, paths with a `..` segment or a NUL byte — all 404 on public.

nginx must **overwrite** forwarding headers rather than append to a caller-supplied chain:

```nginx
location / {
    proxy_pass http://127.0.0.1:8835;
    proxy_set_header X-Forwarded-For $remote_addr;   # not $proxy_add_x_forwarded_for
    proxy_set_header X-Forwarded-Proto https;        # session cookie gets Secure
}
```

### Verify

```bash
# 8000 internal: `/` and `/health` are always anonymous; business APIs key-free with
# security.internal.allow_anonymous: true
curl -o /dev/null -w '%{http_code}\n' http://<lan-ip>:8834/health                                     # 200
curl -o /dev/null -w '%{http_code}\n' http://<lan-ip>:8834/v1/models                                   # 200
# 8001 public: an unlisted path is 404 even with a key (e.g. /api/tags is absent from the sample allowlist)
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/api/tags                                   # 404
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/health                                    # 404
# 8001 public: a LISTED path needs a key (401 without, 200 with) — e.g. GET ^/v1/models$ is listed above
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/v1/models                                 # 401
curl -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" https://<public-host>/v1/models # 200
curl -o /dev/null -w '%{http_code}\n' -X POST https://<public-host>/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'                          # 401
# Management / docs / console are red lines on public — 404, not 403
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/v1/management/overview                   # 404
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/docs                                    # 404
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/admin                                   # 404
curl -o /dev/null -w '%{http_code}\n' https://<public-host>/probe                                    # 404
# A forged XFF cannot unlock anything or change the zone
curl -H 'X-Forwarded-For: 192.168.10.5' -o /dev/null -w '%{http_code}\n' \
  -X POST https://<public-host>/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'                          # 401
curl -m 3 -o /dev/null -w '%{http_code}\n' http://<public-host>:11434/api/tags                      # unreachable
```

If the public socket returns 200 for an unlisted path, `/admin` or `/v1/management/*` answers
anything but 404, or a listed API works without a key, check for an old image, a missing or wrong
`security.public.port` / `security.public.path_allowlist` in the mounted `config.yaml`, a legacy
port variable still in `.env`, or a proxy that appends instead of overwrites XFF.

### Public deployment checklist (post-audit, 2026-11-09)

Code fixes do not replace deployment hardening. Confirm each item on an internet-facing instance:

1. **Terminate TLS at the reverse proxy**: 301 HTTP to HTTPS, send `Strict-Transport-Security`,
   and set `X-Forwarded-Proto https` so the session cookie receives `Secure` (it never does over
   plain HTTP).
2. **Rotate credentials**: use a random `ADMIN_PASSWORD` of 32+ characters (copying the
   `.env.example` value now refuses to start), rotate every `sk-hub-` caller key and every
   provider key in `upstreams[].headers`.
3. **Enable rate limiting**: `security.rate_limit.enabled: true`, or rate-limit at the proxy.
   Sign-in has its own 5-failure / 15-minute lockout; other endpoints do not.
4. **Keep the console internal-only**: `/admin` and `/admin/*` are hard 404 on the public socket
   (there is no admin-shell switch); reach the console only from the LAN/VPN or through a tunnel.
   All `/v1/management/*` endpoints accept a signed session cookie only — a caller API key answers
   401, and they are 404 on public regardless of the config.
5. **Declare the zones in config**: set `security.public.port` to a port distinct from
   `server.port` (e.g. `8001`), map the two container ports to **different host ports**, and
   publish `server.port` on the LAN only. The listening socket is the anchor — no IP list, XFF or
   port environment variable can move a request between zones (legacy port variables are migration
   hints only; remove them from `.env`). List reverse-proxy addresses in
   `security.trusted_proxies` so only their XFF is honoured, using the rightmost untrusted hop.
   Confirm the image starts with `python serve.py` (`proxy_headers=False`), never plain
   `uvicorn main:app`; the latter cannot bind the public port and leaves IP fences
   caller-controlled. A hidden `/docs` must never coexist with a key-free public API.
6. **Reduce the information surface**: `security.internal.expose_version: false`,
   `security.internal.expose_docs: false` unless needed, and keep `security.public.path_allowlist`
   to the minimum set of `re.fullmatch` method+path rules (e.g. `^/v1/chat/completions$`, not a
   broad `^/v1/.*$`). Unlisted pairs are 404 with or without a key. `GET /v1/management/config`
   masks upstream headers (saving the projection restores the real values from disk).
7. **Container / dependencies (deployment-side TODO, no code change)**: the image still runs
   as root and `requirements.txt` uses unbounded `>=`. Prefer a non-root user, a read-only root
   filesystem with only `/data` writable, and a hash-pinned lock file.

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

### Health check returns 401 or 404

Point the probe at the **internal** port (`server.port`, 8000 by default): `/health` and `/` are
always anonymous there, independent of `security.internal.allow_anonymous`. On the public socket
(`security.public.port`) `/health` is a red line and returns **404** unless you explicitly add
`GET ^/health$` to `security.public.path_allowlist` — and even then it needs a key, so it is not a
useful probe. If the instance is public-only (`security.public.port == server.port`), there is
**no anonymous HTTP path at all**: replace the image's HTTP HEALTHCHECK with a TCP connect check on
the listening port, e.g.

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1', 8000)) == 0 else 1)"
```

or add a separate internal listener and probe that.

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

Common causes: no way to authenticate at all (an empty `api_keys` table, `ADMIN_PASSWORD` unset
and no anonymous internal zone — set `ADMIN_PASSWORD`, or set
`security.internal.allow_anonymous: true` for a LAN-only instance), a placeholder `ADMIN_PASSWORD`
copied from `.env.example`, a `config.yaml` Pydantic rejects (e.g. an invalid
`security.public.path_allowlist` method key), or a YAML syntax error. A legacy port variable in
`.env` is only a warning, not a cause — ports come from `config.yaml`. `start-local.bat -CheckOnly`
runs the same preflight without binding any port.

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
