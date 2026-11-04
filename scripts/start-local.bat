@echo off
setlocal EnableExtensions
rem ----------------------------------------------------------------------
rem Start the llm-hub gateway locally (thin wrapper around start-local.ps1).
rem
rem Usage:  scripts\start-local.bat [options]
rem   scripts\start-local.bat                     config port, auto-reload
rem   scripts\start-local.bat -Port 8888 -NoReload
rem   scripts\start-local.bat -CheckOnly           run the preflight only
rem   scripts\start-local.bat -Install             pip install -r requirements.txt first
rem
rem Every argument is forwarded to scripts\start-local.ps1; its header comment
rem documents the full parameter list.
rem ----------------------------------------------------------------------

cd /d "%~dp0.."
if errorlevel 1 (
    echo ERROR: cannot change directory to the repository root.
    exit /b 1
)

where powershell >nul 2>&1
if errorlevel 1 (
    echo ERROR: powershell.exe was not found on PATH.
    echo        Run scripts\start-local.ps1 directly, e.g. from PowerShell 7.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start-local.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" echo.
if not "%RC%"=="0" echo start-local.bat failed with code %RC%.
endlocal & exit /b %RC%
