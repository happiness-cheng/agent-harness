@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo 未找到项目的 .venv\Scripts\python.exe。请先按 docs\ADRIAN.md 安装环境。
    pause
    popd
    exit /b 1
)
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pydantic, httpx" >nul 2>nul
if errorlevel 1 (
    echo Python依赖不完整，请按 docs\ADRIAN.md 安装，未启动Demo。
    pause
    popd
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m min_agent.web --adrian --desktop %*
set "ADRIAN_EXIT_CODE=%ERRORLEVEL%"
if not "%ADRIAN_EXIT_CODE%"=="0" (
    echo Adrian未正常启动或已停止。请查看上方错误；没有回退Demo。
    pause
)
popd
exit /b %ADRIAN_EXIT_CODE%
