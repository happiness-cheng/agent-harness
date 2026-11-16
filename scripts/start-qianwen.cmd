@echo off
rem Double-click also works; the desktop shortcut targets the .ps1.
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-qianwen.ps1" %*