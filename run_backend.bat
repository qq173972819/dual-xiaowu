@echo off
REM 森穹小悟 S3 开放平台 · 一键启动后端
REM 后端默认端口 8104（与 apps/content-console-cn 的默认后端地址一致）
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [首次] 创建虚拟环境并安装依赖...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
echo.
echo ============================================================
echo  小悟后台已启动: http://127.0.0.1:8104
echo  接口文档(Swagger): http://127.0.0.1:8104/docs
echo  演示账号: demo / demo1234
echo  按 Ctrl+C 停止
echo ============================================================
echo.
python -m uvicorn xiaowu_platform.main:app --host 127.0.0.1 --port 8104 --reload
pause
