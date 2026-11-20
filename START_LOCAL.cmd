@echo off
cd /d "%~dp0"
echo Starting n8n...
docker compose -f compose.yaml up -d
if errorlevel 1 goto :fail
echo Building/starting Kinroot website and bridge...
docker compose -f compose.clip-review.yaml up -d --build
if errorlevel 1 goto :fail
echo.
echo n8n: http://localhost:5678
Echo Kinroot Review: http://localhost:8501
pause
exit /b 0
:fail
echo Docker command failed. Make sure Docker Desktop is running.
pause
exit /b 1
