@echo off
setlocal EnableExtensions
rem ----------------------------------------------------------------------
rem Build the llm-hub image as <image>:latest and <image>:<version>.
rem
rem Usage:  scripts\docker-build.bat [image] [version]
rem default image:   bruce1977/llm-hub
rem default version: __version__ from app\main.py
rem ----------------------------------------------------------------------

cd /d "%~dp0.."
if errorlevel 1 (
    echo ERROR: cannot change directory to the repository root.
    exit /b 1
)

set "IMAGE_NAME=%~1"
if not defined IMAGE_NAME set "IMAGE_NAME=bruce1977/llm-hub"
set "VERSION=%~2"

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not reachable. Start Docker Desktop and try again.
    exit /b 1
)

if defined VERSION goto have_version

set "VERSION="
for /f "tokens=2 delims==" %%V in ('findstr /b /c:"__version__" app\main.py') do set "VERSION=%%V"
set VERSION=%VERSION:"=%
for /f "tokens=1" %%V in ("%VERSION%") do set "VERSION=%%V"
if not defined VERSION (
    echo ERROR: could not read __version__ from app\main.py and no version was passed.
    exit /b 1
)

:have_version
echo Image:   %IMAGE_NAME%
echo Version: %VERSION%
echo.

docker build --build-arg VERSION=%VERSION% -t %IMAGE_NAME%:latest -t %IMAGE_NAME%:%VERSION% .
if errorlevel 1 (
    echo.
    echo ERROR: docker build failed.
    exit /b 1
)

echo.
echo Built tags:
echo   %IMAGE_NAME%:latest
echo   %IMAGE_NAME%:%VERSION%
echo.
echo Push them with:  scripts\docker-publish.bat
endlocal
