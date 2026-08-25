@echo off
chcp 65001 >nul
title MsStatTractor 安裝與製作
cd /d "%~dp0"

echo ============================================
echo   MsStatTractor 安裝與製作
echo   (第一次跑一次就好，約 5~15 分鐘)
echo ============================================
echo.

REM ---- 找 Python（偏好 py -3.10，退回 python）----
set "PYCMD="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.10 -c "import sys" >nul 2>nul
    if %errorlevel%==0 set "PYCMD=py -3.10"
)
if not defined PYCMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYCMD=python"
)
if not defined PYCMD (
    echo [錯誤] 找不到 Python。
    echo   請先安裝 Python 3.10：https://www.python.org/downloads/release/python-31011/
    echo   安裝時記得勾選 "Add python.exe to PATH"。
    pause
    exit /b 1
)

echo [1/4] 建立虛擬環境 .venv ...
%PYCMD% -m venv .venv
if errorlevel 1 goto :err

echo [2/4] 安裝所需套件（下載較大，請耐心等）...
".venv\Scripts\pip" install -r requirements.txt
if errorlevel 1 goto :err
".venv\Scripts\python" ".venv\Scripts\pywin32_postinstall.py" -install
".venv\Scripts\pip" install -r requirements-dev.txt
if errorlevel 1 goto :err

echo [3/4] 製作執行檔 ...
call scripts\build_exe.bat
if errorlevel 1 goto :err

echo.
echo ============================================
echo   完成！你的工具在這裡：
echo   %cd%\dist\MsStatTractor\MsStatTractor.exe
echo.
echo   雙擊即可使用。第一次跳出 SmartScreen 警告，
echo   請按「更多資訊 → 仍要執行」。
echo ============================================
pause
exit /b 0

:err
echo.
echo [錯誤] 某一步失敗了。請把視窗中的紅色訊息截圖，
echo   貼給 Alex 的助手看。
pause
exit /b 1
