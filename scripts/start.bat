@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
if not exist ".venv\Scripts\python.exe" (
    echo Please install the project by following README.md first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m min_agent.web --demo %*
if errorlevel 1 pause
