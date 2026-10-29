@echo off
REM ============================================================
REM  Xiaowu backend restart helper (port 8104)
REM  - kills whatever holds port 8104 (stale --reload process)
REM  - restarts xiaowu_platform.main:app with the repo .venv
REM  Keep this file ASCII-only (cmd.exe uses GBK; Chinese = mojibake).
REM  Double-click to run. After it starts, the device's screen
REM  should refresh and show 大盘全景 (A) / 板块动向 (B).
REM ============================================================
cd /d "%~dp0"

echo Releasing port 8104 ...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8104 "') do (
  echo   killing PID %%a
  taskkill /F /PID %%a /T >nul 2>&1
)
timeout /t 2 >nul

if not exist ".venv\Scripts\python.exe" (
  echo [first run] creating venv and installing dependencies...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

echo.
echo ============================================================
echo  Backend : http://0.0.0.0:8104   (LAN: http://192.168.x.x:8104)
echo  Swagger: http://127.0.0.1:8104/docs
echo  Press Ctrl+C to stop
echo ============================================================
echo.
python -m uvicorn xiaowu_platform.main:app --host 0.0.0.0 --port 8104 --reload
