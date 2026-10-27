@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo 未找到本项目虚拟环境，请先按README安装。
    pause
    popd
    exit /b 1
)
set "PYTHONPATH=%CD%\src"
if "%~1"=="" (
    ".venv\Scripts\python.exe" -X utf8 -m min_agent.evaluation.night_cli --help
    pause
    popd
    exit /b 0
)
".venv\Scripts\python.exe" -X utf8 -m min_agent.evaluation.night_cli %*
set "ADRIAN_RUN_EXIT=%ERRORLEVEL%"
popd
exit /b %ADRIAN_RUN_EXIT%
