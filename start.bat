@echo off
title YT Transport Backend
echo Starting YT Transport...
echo.
echo Python will auto-start Go service.
echo Frontend: cd frontend ^&^& npm run dev
echo.
cd /d "%~dp0\ml-service"
.venv\Scripts\python -m uvicorn app.server:app --port 8000
pause
