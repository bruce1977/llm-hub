<#
.SYNOPSIS
    Run the llm-hub gateway on this machine.

.DESCRIPTION
    Starts the FastAPI gateway exactly the way the modules expect (they are flat inside app/,
    so uvicorn must be pointed at app/ with --app-dir) and checks the usual reasons a local
    start fails before launching: missing dependencies, missing or invalid config.json,
    an authenticated gateway with no API key, weak/placeholder keys, a port already in use
    and an unbuilt web console.

    The working directory is app/, like the image's WORKDIR /app, so the relative paths in
    .env (../data/config.json, ../data/.keys) resolve here the same way they do in the
    container.

    Secrets are read from .env (never from config.json) and only their names are printed.

.PARAMETER Port
    Listen port. Default: server.port from the config file (8000 in the template).

.PARAMETER Bind
    Bind address. Default: server.host from the config file.

.PARAMETER Config
    Path to config.json. Default: CONFIG_PATH from .env when it resolves (the value there is
    written as ../data/config.json, relative to app/ as in the image), otherwise
    <repo>\data\config.json - created from data\example.config.json when it does not exist yet.

.PARAMETER EnvFile
    Path to the env file with GATEWAY_API_KEYS / ADMIN_PASSWORD. Default: <repo>\.env.
    Values already set in the current session win, so the shell can override the file.

.PARAMETER Python
    Python executable. Default: python, falling back to the py launcher.

.PARAMETER LogLevel
    uvicorn log level. Default: server.log_level from the config file.

.PARAMETER NoReload
    Serve without --reload (reload watches app/ and restarts on every saved .py file).

.PARAMETER Install
    Run `pip install -r requirements.txt` first, then continue with the checks and start.

.PARAMETER CheckOnly
    Run the preflight, print the verdict and exit without starting the server.

.EXAMPLE
    scripts\start-local.ps1
.EXAMPLE
    scripts\start-local.ps1 -Port 8888 -NoReload
.EXAMPLE
    scripts\start-local.ps1 -CheckOnly
.EXAMPLE
    scripts\start-local.bat -Install
.NOTES
    Endpoints once running:  /health  /docs  /admin/  /v1/models  /v1/chat/completions
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$Bind = '',
    [string]$Config = '',
    [string]$EnvFile = '',
    [string]$Python = '',
    [string]$LogLevel = '',
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
# The image runs with WORKDIR /app, so every relative path the app receives (CONFIG_PATH and
# GATEWAY_API_KEYS_FILE from .env, e.g. ../data/config.json) is read from inside app/.
# The launcher keeps that convention: app/ is the working directory for both the probe and
# uvicorn, which is also what the README's `cd app` commands assume.
$Root = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $Root 'app'
$ReqFile = Join-Path $Root 'requirements.txt'

if (-not (Test-Path (Join-Path $AppDir 'main.py'))) {
    Fail "cannot find $AppDir\main.py - run this script from inside the repository."
}

# -Config wins over CONFIG_PATH from .env, which wins over the repository default.
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
$DefaultConfig = Join-Path $Root 'data\config.json'
$ConfigPath = $DefaultConfig
if ($ConfigExplicit) {
    # a path the caller typed is read from where they are standing
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

$Template = Join-Path $Root 'data\example.config.json'
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    if (Test-Path -LiteralPath $Template) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
        Copy-Item -LiteralPath $Template -Destination $ConfigPath
        Warn "created $ConfigPath from data\example.config.json - set the real upstream URLs before relying on it."
    } else {
        Fail "config file not found: $ConfigPath (and no data\example.config.json to copy)."
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

# The keys file is opened by the app relative to its working directory. Say it out loud when
# it is not there instead of letting the app log a read error on every start and reload.
$KeysFile = [string] $env:GATEWAY_API_KEYS_FILE
if ($KeysFile -and -not (Test-Path -LiteralPath (Resolve-FromPath $KeysFile $AppDir))) {
    if ($env:GATEWAY_API_KEYS) {
        Remove-Item -LiteralPath 'Env:GATEWAY_API_KEYS_FILE'
        Warn ("GATEWAY_API_KEYS_FILE='{0}' is not readable from {1} - using the inline GATEWAY_API_KEYS instead." -f $KeysFile, $AppDir)
    } else {
        Warn ("GATEWAY_API_KEYS_FILE='{0}' is not readable from {1} and no inline GATEWAY_API_KEYS are set - auth will have no keys." -f $KeysFile, $AppDir)
    }
}

# ----------------------------------------------------------------- preflight ---
# The verdict is computed by the app's own modules so the rules cannot drift: config.json is
# parsed by the real Pydantic models and the keys are screened by auth.find_weak_keys.
$Probe = @"
import importlib.util
import json
import logging
import os
import sys

MARKER = 'LLMHUB_PREFLIGHT='
APP_DIR = sys.argv[1]
sys.path.insert(0, APP_DIR)
# The image runs with WORKDIR /app and the launcher uses the same working directory, so
# relative paths from .env resolve identically here and at launch.
os.chdir(APP_DIR)
# The app writes its own warnings to stderr; the verdict is the only output wanted here.
logging.disable(logging.CRITICAL)

report = {}

missing = []
for module in ('fastapi', 'uvicorn', 'httpx', 'pydantic'):
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
    from auth import find_weak_keys
    from config import ConfigManager

    cfg = ConfigManager().config
except Exception as exc:  # a broken config.json must be reported, not traced
    report['config_error'] = '%s: %s' % (type(exc).__name__, str(exc).splitlines()[0] if str(exc) else '')
    print(MARKER + json.dumps(report))
    sys.exit(0)

report.update({
    'host': cfg.server.host,
    'port': cfg.server.port,
    'log_level': cfg.server.log_level,
    'upstreams': len(cfg.upstreams),
    'aliases': len(cfg.aliases),
    'auth_enabled': bool(cfg.auth.enabled),
    'keys': len(cfg.auth.api_keys),
    'weak': find_weak_keys(cfg.auth.api_keys),
    'fail_on_weak': bool(cfg.security.fail_on_weak_keys),
    'request_log': bool(cfg.request_log.enabled),
    'admin_password': bool(os.environ.get('ADMIN_PASSWORD', '').strip()),
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
Note ("upstreams={0}  aliases={1}  auth={2}  keys={3}  request_log={4}" -f `
    $Check.upstreams, $Check.aliases, $(if ($Check.auth_enabled) { 'enabled' } else { 'disabled' }), $Check.keys, $Check.request_log)

# Startup refuses to serve an authenticated gateway without any key - say it here instead.
if ($Check.auth_enabled -and $Check.keys -eq 0) {
    Fail ("auth.enabled is true but no API key is available. Put GATEWAY_API_KEYS=sk-... in $([System.IO.Path]::GetFileName($EnvFile)), " +
         "or GATEWAY_API_KEYS_FILE, or set auth.enabled=false in the config.")
}

$Weak = @()
if ($Check.weak) { $Weak = @($Check.weak) }
if ($Weak.Count -gt 0) {
    $list = $Weak -join '; '
    if ($Check.fail_on_weak) {
        Fail ("weak/placeholder API keys would stop the gateway from booting (security.fail_on_weak_keys=true): $list. " +
             "Use keys of 16+ random chars, or set fail_on_weak_keys=false to only warn.")
    }
    Warn "weak/placeholder API keys detected (startup would only warn): $list"
}

if (-not $Check.admin_password) {
    Warn 'ADMIN_PASSWORD is unset - the /admin console and every /v1/management route answer 503.'
}

if (-not (Test-Path (Join-Path $AppDir 'web\index.html'))) {
    Warn 'app\web\index.html is missing, so /admin will answer 503. Build it with: cd web; npm install; npm run build'
}

# ------------------------------------------------------------------ endpoint ---
if ($Port -le 0) { $Port = $Check.port }
if (-not $Bind) { $Bind = $Check.host }
if (-not $LogLevel) { $LogLevel = $Check.log_level }

try {
    $Listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
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
    Fail ("port $Port is already in use by " + ($Names -join ', ') + ". Stop it or pass another -Port.")
}

$Reload = -not $NoReload
if ($Reload -and -not $Check.watchfiles) {
    Warn 'watchfiles is not installed - starting without auto-reload.'
    $Reload = $false
}

$DisplayHost = $Bind
if ($DisplayHost -eq '0.0.0.0' -or $DisplayHost -eq '::' -or $DisplayHost -eq '*') { $DisplayHost = '127.0.0.1' }
$BaseUrl = "http://${DisplayHost}:$Port"

Step 'ready'
Note "gateway  $BaseUrl"
Note "docs     $BaseUrl/docs"
Note "console  $BaseUrl/admin/"
Note "models   $BaseUrl/v1/models   (needs an API key header)"

if ($CheckOnly) {
    Write-Host ''
    Write-Host 'Preflight passed - nothing started (-CheckOnly).' -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------- launch ---
$UvicornArgs = @('main:app', '--app-dir', $AppDir, '--host', $Bind, '--port', "$Port", '--log-level', $LogLevel)
if ($Reload) { $UvicornArgs += @('--reload', '--reload-dir', $AppDir) }

Write-Host ''
Step "uvicorn $($UvicornArgs -join ' ')"
Note 'press Ctrl-C to stop'

# The working directory is app/, exactly the image's WORKDIR /app, so the relative paths written
# in .env (../data/config.json, ../data/.keys) and next to config.json resolve the same way here.
Push-Location -LiteralPath $AppDir
try {
    & $PyExe @PyPrefix -m uvicorn @UvicornArgs
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($Code -ne 0) { Write-Host "gateway exited with code $Code" -ForegroundColor Red }
exit $Code
