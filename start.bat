@echo off
title OCR Studio
echo ===================================================
echo   [OCR Studio] Starting application...
echo ===================================================
echo Activating Python virtual environment...
call venv\Scripts\activate

echo Launching Frontend in default browser...
start http://localhost:8080

echo Starting FastAPI Backend Server on port 8080...
uvicorn backend.main:app --host 127.0.0.1 --port 8080

pause
