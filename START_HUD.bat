@echo off
chcp 65001 >nul
title MSLevelingTool 啟動
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 還沒安裝。請先雙擊 BUILD_MSLevelingTool.bat 一次。
    pause
    exit /b 1
)

".venv\Scripts\python" scripts\run_overlay.py
pause
