@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m allotment worker %*
) else (
  python -m allotment worker %*
)
exit /b %errorlevel%
