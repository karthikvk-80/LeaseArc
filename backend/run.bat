@echo off
setlocal

cd /d %~dp0

set "BACKEND_DIR=%CD%"
set "SITE_PACKAGES=%BACKEND_DIR%\venv\Lib\site-packages"
set "PYTHON_EXE="

for /f "tokens=2,* delims== " %%A in ('findstr /b /c:"executable" venv\pyvenv.cfg 2^>nul') do (
  if not defined PYTHON_EXE set "PYTHON_EXE=%%A"
)

if not defined PYTHON_EXE set "PYTHON_EXE=python"

set "PYTHONPATH=%BACKEND_DIR%;%SITE_PACKAGES%;%PYTHONPATH%"
"%PYTHON_EXE%" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
