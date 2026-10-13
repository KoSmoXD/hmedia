@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel% equ 0 (set "PYTHON=py") else (set "PYTHON=python")
if not exist .venv "%PYTHON%" -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Python package installation failed.
  pause
  exit /b 1
)
echo.
echo Mr H Media: http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
