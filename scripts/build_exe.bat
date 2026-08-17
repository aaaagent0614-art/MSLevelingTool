@echo off
REM Build the standalone Windows .exe. Can be run from anywhere, e.g.:
REM   scripts\build_exe.bat
REM   (or double-click it from inside scripts\)
REM
REM Requires the venv already set up (see README "Installation") plus
REM PyInstaller installed into it (pip install -r requirements-dev.txt).
REM Output: dist\MapleStoryAnalyer\MapleStoryAnalyer.exe (relative to repo root)

REM cd to the repo root regardless of where this .bat was invoked from.
pushd "%~dp0.."

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo PyInstaller not found in .venv. Run:
    echo   .venv\Scripts\pip install -r requirements-dev.txt
    popd
    exit /b 1
)

.venv\Scripts\pyinstaller scripts\maple_analyzer.spec --noconfirm
if %ERRORLEVEL% neq 0 (
    echo Build failed.
    popd
    exit /b %ERRORLEVEL%
)
echo.
echo Build complete: dist\MapleStoryAnalyer\MapleStoryAnalyer.exe
popd
