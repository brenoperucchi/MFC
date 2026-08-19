@echo off
title CSS Pro Institutional Web Platform - Localhost :8050
echo ========================================================
echo   INICIANDO CSS PRO INSTITUTIONAL WEB PLATFORM (8050)
echo ========================================================
echo.

cd /d "%~dp0"

echo [1/2] Iniciando servidor FastAPI e Web Dashboard...
start "" "http://localhost:8050"
python web\server.py

pause
