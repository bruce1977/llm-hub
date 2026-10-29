@echo off
setlocal EnableExtensions
rem ----------------------------------------------------------------------
rem Build and publish the llm-hub image to Docker Hub.
rem
rem Runs docker_build.bat, then pushes <image>:latest and <image>:<version>.
rem Nothing is committed or pushed to git - do that yourself first.
rem
rem Usage:  scripts\docker_publish.bat [image] [version]
rem default image:   bruce1977/llm-hub
rem default version: __version__ from app\main.py
rem ----------------------------------------------------------------------

cd /d "%~dp0.."

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
echo ====================================================
echo llm-hub publish
echo   image:   %IMAGE_NAME%
echo   version: %VERSION%
echo ====================================================
echo.

call "%~dp0docker_build.bat" "%IMAGE_NAME%" "%VERSION%"
if errorlevel 1 (
    echo ERROR: build failed, nothing was pushed.
    exit /b 1
)

echo.
echo Pushing %IMAGE_NAME%:latest
docker push %IMAGE_NAME%:latest
if errorlevel 1 (
    echo ERROR: docker push failed. Are you logged in?  docker login
    exit /b 1
)

echo.
echo Pushing %IMAGE_NAME%:%VERSION%
docker push %IMAGE_NAME%:%VERSION%
if errorlevel 1 (
    echo ERROR: docker push failed. Are you logged in?  docker login
    exit /b 1
)

echo.
echo Published:
echo   %IMAGE_NAME%:latest
echo   %IMAGE_NAME%:%VERSION%
echo https://hub.docker.com/r/%IMAGE_NAME%
endlocal
