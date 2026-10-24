@echo off
REM Xiaowu S3 Open Platform - one-click backend launcher
REM Backend port 8104, matching apps/content-console-cn proxy default.
REM NOTE: keep this file ASCII-only. cmd.exe parses .bat with GBK codepage,
REM any Chinese here becomes mojibake and breaks execution.
cd /d "%~dp0"
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
echo  Backend:  http://0.0.0.0:8104   (LAN: http://192.168.x.x:8104)
echo  Swagger:  http://127.0.0.1:8104/docs
echo  Admin account: configure XIAOWU_ADMIN_USERNAME and XIAOWU_ADMIN_PASSWORD first
echo  Press Ctrl+C to stop
echo ============================================================
echo.
REM --host MUST be 0.0.0.0: 127.0.0.1:8104 is occupied by a hidden loopback
REM forward on this machine, and the firmware reaches the backend via LAN IP.
python -m uvicorn xiaowu_platform.main:app --host 0.0.0.0 --port 8104 --reload
pause
