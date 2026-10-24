@echo off
REM ============================================================
REM  Xiaowu AI local dev launcher (backend + content console)
REM  Double-click to run.  Backend :8092   Frontend :8094
REM  Already-running ports are skipped (no conflict).
REM  Close the black window to stop that service.
REM  To change Node version, edit NODE_BIN below.
REM ============================================================

set "ROOT=E:\electron\screen\inksight-main\inksight-main\xiaowu-s3-open-platform"
set "NODE_BIN=C:\Users\upisq\.workbuddy\binaries\node\versions\22.22.2-6"
set "BACKEND_PORT=8092"
set "FRONTEND_PORT=8094"

REM ---- backend (FastAPI, port 8092) ----
netstat -ano 2>nul | findstr ":%BACKEND_PORT% " >nul
if %errorlevel%==0 (
    echo [backend ] port %BACKEND_PORT% already in use, skip
) else (
    echo [backend ] starting backend on port %BACKEND_PORT%...
    start "xiaowu-backend" /D "%ROOT%" cmd /k "%ROOT%\.venv\Scripts\python.exe -m uvicorn xiaowu_platform.main:app --host 0.0.0.0 --port %BACKEND_PORT%"
)

REM ---- frontend (Next.js content console, port 8094) ----
netstat -ano 2>nul | findstr ":%FRONTEND_PORT% " >nul
if %errorlevel%==0 (
    echo [frontend] port %FRONTEND_PORT% already in use, skip
) else (
    echo [frontend] starting frontend on port %FRONTEND_PORT%...
    start "xiaowu-frontend" /D "%ROOT%\apps\content-console-cn" cmd /k "set PATH=%NODE_BIN%;%%PATH%% && set INKSIGHT_BACKEND_API_BASE=http://127.0.0.1:8092 && .\node_modules\.bin\next dev -p %FRONTEND_PORT%"
)

echo.
echo Done.
echo   backend : http://localhost:8092
echo   frontend: http://localhost:8094
echo Close the black window to stop that service.
pause
