<#
.SYNOPSIS
    Run the llm-hub gateway on this machine (development hot reload or two-zone listeners).

.DESCRIPTION
    Starts the FastAPI gateway from app/ (the modules are flat, so the app directory must be
    on sys.path) and checks the usual reasons a local start fails before launching: missing
    dependencies, missing or invalid config.yaml, an empty key table with no way to mint
    a key, a busy listener port, a missing console build or an unset admin password.

    Ports and zone policy come ONLY from config.yaml:
      server.port              internal (trusted LAN) listener
      server.host              bind address
      server.log_level         uvicorn log level
      security.public.port     public (internet-facing) listener, or null for none.
                               Equal to server.port = public-only instance.

    Two launch paths, picked automatically:

    * Default (no distinct public port, no -NoReload): uvicorn CLI with auto-reload. It passes
      --no-proxy-headers, so a caller's X-Forwarded-For can no longer rewrite the TCP peer.
      Single listener only; the same HTTP policy as the container.

    * A distinct public port, or -NoReload: app/serve.py, the hardened entrypoint also used
      by the Docker image. It binds every listener in ONE process (shared API-key cache, rate
      buckets and login lockout) and uses the kernel-assigned local socket as the trust
      anchor. Public traffic needs a key and only sees the paths in
      security.public.path_allowlist. serve.py has no auto-reload; restart after app/*.py edits.

    The working directory is app/, like the image's WORKDIR /app, so the relative path in
    .env (../data/config.yaml) resolves here the same way it does in the container.

    Secrets are read from .env (never from config.yaml) and only their names are printed.

.PARAMETER Config
    Path to config.yaml. Default: CONFIG_PATH from .env when it resolves (the value there is
    written as ../data/config.yaml, relative to app/ as in the image), otherwise
    <repo>\data\config.yaml - created from data\config.example.yaml when it does not exist yet.

.PARAMETER EnvFile
    Path to the env file with CONFIG_PATH / ADMIN_PASSWORD / SYSTEMONE_SERVER_API_KEY.
    Default: <repo>\.env.
    Values already set in the current session win, so the shell can override the file.

.PARAMETER Python
    Python executable. Default: python, falling back to the py launcher.

.PARAMETER NoReload
    Skip auto-reload and start through app/serve.py, the hardened entrypoint the Docker
    image uses. Useful to validate the exact production startup path locally.

.PARAMETER Install
    Run `pip install -r requirements.txt` first, then continue with the checks and start.

.PARAMETER CheckOnly
    Run the preflight, print the verdict and exit without starting the server.

.EXAMPLE
    scripts\start-local.ps1
.EXAMPLE
    scripts\start-local.ps1 -NoReload
.EXAMPLE
    scripts\start-local.ps1 -CheckOnly
.EXAMPLE
    scripts\start-local.ps1 -Install
.NOTES
    Edit data\config.yaml to open a public listener:
      security:
        public:
          port: 8001
          path_allowlist:
            GET:  ['^/v1/models$']
            POST: ['^/v1/chat/completions$']
    Then map two DIFFERENT container ports to the host. Two host ports pointing at the
    same container port do NOT create two zones.
#>
[CmdletBinding()]
param(
    [string]$Config = '',
    [string]$EnvFile = '',
    [string]$Python = '',
    [switch]$NoReload,
    [switch]$Install,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Note([string]$Message) { Write-Host "    $Message" -ForegroundColor DarkGray }
function Warn([string]$Message) { Write-Host "WARNING: $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------- layout ---
# The image runs with WORKDIR /app, so every relative path the app receives (CONFIG_PATH from
# .env, e.g. ../data/config.yaml) is read from inside app/. The launcher keeps that convention.
$Root = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $Root 'app'
$ReqFile = Join-Path $Root 'requirements.txt'

if (-not (Test-Path (Join-Path $AppDir 'main.py'))) {
    Fail "cannot find $AppDir\main.py - run this script from inside the repository."
}

$ConfigExplicit = [bool]$Config
if (-not $EnvFile) { $EnvFile = Join-Path $Root '.env' }

function Resolve-FromPath([string]$Path, [string]$Base) {
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Base $Path }
    [System.IO.Path]::GetFullPath($Path)
}

# --------------------------------------------------------------- python probe ---
if ($Python) {
    $PyName = $Python
    $PyPrefix = @()
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyName = 'python'
    $PyPrefix = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    # The Windows launcher needs -3 to avoid a stray Python 2.
    $PyName = 'py'
    $PyPrefix = @('-3')
} else {
    Fail "neither 'python' nor 'py' is on PATH. Install Python 3.12+ or pass -Python <exe>."
}

$PyExe = (Get-Command $PyName -ErrorAction SilentlyContinue).Source
if (-not $PyExe) { Fail "could not resolve the python executable '$PyName'." }

$PyVersion = ''
try { $PyVersion = (& $PyExe @PyPrefix -c 'import sys; print(sys.version.split()[0])') | Select-Object -Last 1 } catch { $PyVersion = 'unknown' }
$PrefixText = if ($PyPrefix.Count) { " $($PyPrefix -join ' ')" } else { '' }
Step "python: $PyName$PrefixText - $PyVersion"

# ------------------------------------------------------------------ install ---
if ($Install) {
    if (-not (Test-Path $ReqFile)) { Fail "missing $ReqFile" }
    Step 'installing dependencies from requirements.txt'
    & $PyExe @PyPrefix -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }
}

# -------------------------------------------------------------------- .env ----
# Read before the config is located, because CONFIG_PATH may come from this file. Nothing is
# exported until the final path is known, and a variable already set in the session wins.
$EnvValues = [ordered] @{}
if (Test-Path -LiteralPath $EnvFile) {
    Step "env file: $EnvFile"
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        $text = $line.Trim()
        if (-not $text) { continue }
        if ($text.StartsWith('#')) { continue }
        if ($text.StartsWith('export ')) { $text = $text.Substring(7).Trim() }
        $idx = $text.IndexOf('=')
        if ($idx -lt 1) { continue }
        $name = $text.Substring(0, $idx).Trim()
        $value = $text.Substring($idx + 1).Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        if (-not $EnvValues.Contains($name)) { $EnvValues[$name] = $value }
    }
} else {
    Warn "no env file at $EnvFile - keys must come from the current environment (copy .env.example to .env)."
}

# --------------------------------------------------------------------- config ---
$DefaultConfig = Join-Path $Root 'data\config.yaml'
$ConfigPath = $DefaultConfig
if ($ConfigExplicit) {
    $ConfigPath = Resolve-FromPath $Config (Get-Location).ProviderPath
} elseif ($EnvValues.Contains('CONFIG_PATH')) {
    $FromEnv = [string] $EnvValues['CONFIG_PATH']
    $Candidate = Resolve-FromPath $FromEnv $AppDir
    if (Test-Path -LiteralPath $Candidate) {
        $ConfigPath = $Candidate
    } else {
        Note ("CONFIG_PATH='{0}' from .env does not resolve from {1} - using {2}" -f $FromEnv, $AppDir, $DefaultConfig)
    }
}

$Template = Join-Path $Root 'data\config.example.yaml'
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    $Legacy = Join-Path (Split-Path -Parent $ConfigPath) 'config.json'
    if (Test-Path -LiteralPath $Legacy) {
        Warn ("{0} not found - using the legacy {1}; rename it to {2}." -f $ConfigPath, $Legacy, [System.IO.Path]::GetFileName($ConfigPath))
        $ConfigPath = $Legacy
    } elseif (Test-Path -LiteralPath $Template) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
        Copy-Item -LiteralPath $Template -Destination $ConfigPath
        Warn "created $ConfigPath from data\config.example.yaml - set the real upstream URLs before relying on it."
    } else {
        Fail "config file not found: $ConfigPath (and no data\config.example.yaml to copy)."
    }
}
Step "config: $ConfigPath"

$EnvNames = @()
foreach ($name in $EnvValues.Keys) {
    if (Test-Path "Env:$name") {
        $EnvNames += "$name(kept from shell)"
        continue
    }
    Set-Item -Path "Env:$name" -Value ([string] $EnvValues[$name])
    $EnvNames += $name
}
if ($EnvNames.Count) { Note ("loaded: " + ($EnvNames -join ', ')) }

$env:CONFIG_PATH = $ConfigPath

# Caller keys live only in the SQLite api_keys table and are minted in the console. Both
# environment seed variables are dead: GATEWAY_API_KEYS (inline) and GATEWAY_API_KEYS_FILE
# (file) were removed, so drop them from .env and from the environment to avoid the
# impression that they still matter.
foreach ($DeadKeyVar in @('GATEWAY_API_KEYS', 'GATEWAY_API_KEYS_FILE')) {
    if (Test-Path "Env:$DeadKeyVar") {
        Remove-Item -LiteralPath "Env:$DeadKeyVar"
        Warn ("$DeadKeyVar is no longer supported - caller keys are minted at /admin and stored in the SQLite api_keys table; ignoring the stale variable.")
    }
}

# Ports and zone policy now live in config.yaml. The app still reads the legacy variables
# once as a migration hint (LLMHUB_PUBLIC_PORTS -> security.public.port,
# GATEWAY_AUTH_DISABLED -> security.internal.allow_anonymous) but explicit config wins.
$LegacyNames = @('LLMHUB_HOST','LLMHUB_PORT','LLMHUB_PUBLIC_PORTS','LLMHUB_PUBLIC_PORT','LLMHUB_PUBLIC_ADMIN','LLMHUB_LOG_LEVEL','GATEWAY_AUTH_DISABLED')
$LegacyPresent = @($LegacyNames | Where-Object { (Get-Item "Env:$_" -ErrorAction SilentlyContinue) })
if ($LegacyPresent.Count -gt 0) {
    Warn ('legacy listener variable(s) in the environment: ' + ($LegacyPresent -join ', ') + '. Ports/zones now come from config.yaml; set server.port / security.public.port there and remove these variables.')
}

# ----------------------------------------------------------------- preflight ---
# The verdict is computed by the app's own modules so the rules cannot drift: config.yaml is
# parsed by the real Pydantic models, the api_keys row count is read from llmhub.db, and
# the listener set is Config.listen_ports() - the exact same values serve.py will bind.
$Probe = @"
import importlib.util
import json
import logging
import os
import sys

MARKER = 'LLMHUB_PREFLIGHT='
APP_DIR = sys.argv[1]
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)
# The app writes its own warnings to stderr; the verdict is the only output wanted here.
logging.disable(logging.CRITICAL)

report = {}

missing = []
for module in ('fastapi', 'uvicorn', 'httpx', 'pydantic', 'yaml'):
    try:
        __import__(module)
    except ImportError:
        missing.append(module)
report['missing_deps'] = missing
report['watchfiles'] = importlib.util.find_spec('watchfiles') is not None

if missing:
    print(MARKER + json.dumps(report))
    sys.exit(0)

try:
    from config import ConfigManager, LEGACY_LISTENER_ENV

    cfg = ConfigManager().config
except Exception as exc:  # a broken config.yaml must be reported, not traced
    report['config_error'] = '%s: %s' % (type(exc).__name__, str(exc).splitlines()[0] if str(exc) else '')
    print(MARKER + json.dumps(report))
    sys.exit(0)

public_port = cfg.security.public.port
listen_ports = cfg.listen_ports()
internal_ports = [p for p in listen_ports if public_port is None or p != public_port]

# Caller keys are minted in the console and live only in this table. Count the rows
# without starting the app so the verdict matches the lifespan guard.
key_rows = 0
try:
    from key_store import ApiKeyStore
    db_path = ApiKeyStore().db_path
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            key_rows = int(conn.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0])
        finally:
            conn.close()
except Exception:
    key_rows = 0

allow_anonymous = bool(cfg.security.internal.allow_anonymous)
report.update({
    'host': cfg.server.host,
    'port': cfg.server.port,
    'log_level': cfg.server.log_level,
    'listen_ports': listen_ports,
    'public_port': public_port,
    'internal_ports': internal_ports,
    'upstreams': len(cfg.upstreams),
    'aliases': len(cfg.aliases),
    'keys': key_rows,
    'request_log': bool(cfg.request_log.enabled),
    'admin_password': bool(os.environ.get('ADMIN_PASSWORD', '').strip()),
    'allow_anonymous': allow_anonymous,
    'expose_docs': bool(cfg.security.internal.expose_docs),
    'public_rules': sum(len(v) for v in cfg.security.public.path_allowlist.values()),
    'legacy_env': [name for name in LEGACY_LISTENER_ENV if os.getenv(name, '').strip()],
    # Mirrors the lifespan guard: an empty key table is only fatal when the console
    # cannot mint one either (no ADMIN_PASSWORD) and no anonymous zone exists.
    'fail_closed': (key_rows == 0
                    and not os.environ.get('ADMIN_PASSWORD', '').strip()
                    and not (internal_ports and allow_anonymous)),
})
print(MARKER + json.dumps(report))
"@

$ProbeFile = Join-Path $env:TEMP ("llmhub-preflight-{0}.py" -f $PID)
$ProbeErr = Join-Path $env:TEMP ("llmhub-preflight-{0}.err" -f $PID)
$ProbeCode = 0
try {
    Set-Content -LiteralPath $ProbeFile -Value $Probe -Encoding ASCII
    # stderr goes to a file, not into the pipeline: with ErrorActionPreference=Stop, PowerShell 5.1
    # turns every redirected native stderr line into an error record and aborts the script.
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $Raw = & $PyExe @PyPrefix $ProbeFile $AppDir 2>$ProbeErr
        $ProbeCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($ProbeCode -ne 0) {
        if (Test-Path -LiteralPath $ProbeErr) { Write-Host (Get-Content -LiteralPath $ProbeErr -Raw).Trim() -ForegroundColor Red }
        Fail "the preflight probe exited with code $ProbeCode."
    }
} finally {
    Remove-Item -LiteralPath $ProbeFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ProbeErr -Force -ErrorAction SilentlyContinue
}

$Line = @($Raw | Where-Object { $_ -like 'LLMHUB_PREFLIGHT=*' } | Select-Object -Last 1)
if (-not $Line) {
    Write-Host ($Raw | Out-String).Trim()
    Fail 'the preflight probe produced no verdict.'
}
$Check = ($Line[0] -replace '^LLMHUB_PREFLIGHT=', '') | ConvertFrom-Json

if (@($Check.missing_deps).Count -gt 0) {
    Fail ("missing dependencies: " + ($Check.missing_deps -join ', ') + ". Run this script with -Install or: $PyName -m pip install -r `"$ReqFile`"")
}
if ($Check.config_error) { Fail "config rejected by the app's own validator - $($Check.config_error)" }

Step 'preflight'
Note ("upstreams={0}  aliases={1}  listen=[{2}]  public={3}  keys={4}  request_log={5}" -f `
    $Check.upstreams, $Check.aliases, ($Check.listen_ports -join ','), $Check.public_port, $Check.keys, $Check.request_log)

# The lifespan guard refuses to start in this state - say it here instead of after the bind.
# An empty table alone is fine (the console mints the key); it is fatal only when there is
# also no ADMIN_PASSWORD and no anonymous internal zone, i.e. no credential can ever exist.
if ($Check.fail_closed) {
    Fail ("the api_keys table is empty, ADMIN_PASSWORD is unset and there is no anonymous internal " +
          "zone - no credential can exist. Put ADMIN_PASSWORD=<32+ random chars> in " +
          "$([System.IO.Path]::GetFileName($EnvFile)) and mint the first key at /admin, or set " +
          "security.internal.allow_anonymous=true in config.yaml for a LAN-only instance.")
}

if (-not $Check.admin_password) {
    Warn 'ADMIN_PASSWORD is unset - the /admin console and every /v1/management route answer 503.'
}

if (-not (Test-Path (Join-Path $AppDir 'web\index.html'))) {
    Warn 'app\web\index.html is missing, so /admin will answer 503. Build it with: cd web; npm install; npm run build'
}

# ------------------------------------------------------------------ endpoint ---
# serve.py owns every listener in one process; the uvicorn CLI binds only one. Never leave a
# declared public port unbound - that would silently drop the internet-facing listener.
$UseServe = $NoReload -or ($Check.public_port -and $Check.public_port -ne $Check.port)
$Reload = -not $UseServe
if ($Reload -and -not $Check.watchfiles) {
    Warn 'watchfiles is not installed - starting without auto-reload.'
    $Reload = $false
}

$Bind = [string] $Check.host
$Port = [int] $Check.port
$LogLevel = [string] $Check.log_level
$AllPorts = @($Check.listen_ports | ForEach-Object { [int]$_ } | Sort-Object -Unique)
foreach ($listenPort in $AllPorts) {
    try {
        $Listeners = @(Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue)
    } catch {
        $Listeners = @()
    }
    if ($Listeners.Count -gt 0) {
        $Owners = ($Listeners | ForEach-Object { $_.OwningProcess } | Select-Object -Unique)
        $Names = @()
        foreach ($procId in $Owners) {
            $procName = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            $Names += ("pid {0}{1}" -f $procId, $(if ($procName) { " ($procName)" } else { '' }))
        }
        Fail ("port $listenPort is already in use by " + ($Names -join ', ') + ". Stop it or edit server.port / security.public.port in config.yaml.")
    }
}

$DisplayHost = $Bind
if ($DisplayHost -eq '0.0.0.0' -or $DisplayHost -eq '::' -or $DisplayHost -eq '*') { $DisplayHost = '127.0.0.1' }
$BaseUrl = "http://${DisplayHost}:$Port"

Step 'ready'
if ($Check.public_port) {
    $KeyText = if ($Check.allow_anonymous) { 'key-free' } else { 'key-required' }
    Note "internal  $BaseUrl  ($KeyText APIs; /docs and /admin reachable; management API is cookie-only)"
    if ($Check.public_port -eq $Check.port) {
        Note "public    $BaseUrl  (SAME socket - public-only instance, no anonymous HTTP path)"
    } else {
        Note "public    http://${DisplayHost}:$($Check.public_port)  ($($Check.public_rules) configured method+path rule(s); every request needs a key)"
    }
}
Note "config    server.port=$Port  security.public.port=$($Check.public_port)  listen=[$($AllPorts -join ',')]"
if (@($Check.legacy_env).Count -gt 0) {
    Note "legacy env still set: $($Check.legacy_env -join ', ') (app migrates/ignores it; remove it from .env)"
}

if ($UseServe) {
    Note 'entry     python serve.py  (zone-aware, proxy_headers=False, no auto-reload)'
} else {
    Note "gateway   $BaseUrl"
    Note "docs      $BaseUrl/docs"
    Note "console   $BaseUrl/admin/"
    Note 'entry     uvicorn --no-proxy-headers  (single internal listener; no IP header rewrite)'
}

if ($CheckOnly) {
    Write-Host ''
    Write-Host 'Preflight passed - nothing started (-CheckOnly).' -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------- launch ---
# The working directory is app/, exactly the image's WORKDIR /app, so the relative path
# written in .env (../data/config.yaml) resolves the same way here and in the container.
Push-Location -LiteralPath $AppDir
try {
    if ($UseServe) {
        Step 'python serve.py  (hardened, zone-aware)'
        Note 'press Ctrl-C to stop'
        & $PyExe @PyPrefix serve.py
        $Code = $LASTEXITCODE
    } else {
        # --no-proxy-headers is the local equivalent of serve.py's proxy_headers=False:
        # without it ProxyHeadersMiddleware rewrites scope["client"] from a caller's XFF.
        $UvicornArgs = @('main:app', '--app-dir', $AppDir, '--host', $Bind, '--port', "$Port",
                         '--log-level', $LogLevel, '--no-proxy-headers',
                         '--log-config', 'logging.json')
        if ($Reload) { $UvicornArgs += @('--reload', '--reload-dir', $AppDir) }
        Write-Host ''
        Step "uvicorn $($UvicornArgs -join ' ')"
        Note 'press Ctrl-C to stop'
        & $PyExe @PyPrefix -m uvicorn @UvicornArgs
        $Code = $LASTEXITCODE
    }
} finally {
    Pop-Location
}

if ($Code -ne 0) { Write-Host "gateway exited with code $Code" -ForegroundColor Red }
exit $Code
