@echo off
setlocal
rem This .bat lives at the repo root, but the server (ahkaiving package)
rem must run from server\ so Python can import it.
cd /d "%~dp0server"
title AH!chiving agent server
echo ============================================
echo   AH!chiving agent  (server\ahkaiving)
echo ============================================
echo.

rem --- 1) find Python (py launcher / PATH / anaconda / miniconda) ---
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set PY="%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set PY="%USERPROFILE%\miniconda3\python.exe"
if not defined PY (
  echo [!] Python 3.10+ not found. Install it from https://www.python.org
  pause
  exit /b 1
)

rem --- 2) first run: create venv + install deps into server\.venv ---
if not exist ".venv\Scripts\python.exe" (
  echo [setup] First run: creating a virtual env and installing packages.
  echo         This can take a few minutes...
  %PY% -m venv .venv || (echo [!] venv creation failed. Need Python 3.10+. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [!] install failed. Check your internet connection. & pause & exit /b 1)
) else (
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >nul 2>nul
)

rem --- 3) open the dashboard, then start the server (blocking) ---
echo.
echo [ready] Dashboard: http://127.0.0.1:8800     (press Ctrl+C to stop)
echo.
start "" http://127.0.0.1:8800/
".venv\Scripts\python.exe" -m uvicorn ahkaiving.api.app:app --host 127.0.0.1 --port 8800

echo.
echo [stopped] server exited.
pause
