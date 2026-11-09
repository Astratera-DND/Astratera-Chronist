@echo off
rem ============================================================================
rem  Astratera Chronist - build a Windows .exe on this computer
rem
rem    build_chronist.bat            one single exe   (dist\AstrateraChronist.exe)
rem    build_chronist.bat folder     folder build     (dist\AstrateraChronist\)  starts faster
rem
rem  Needs Python 3.9+ (python.org).  Everything else is installed into a private
rem  virtual environment (.venv-build) inside this folder.
rem ============================================================================
setlocal
cd /d "%~dp0"
title Astratera Chronist - build exe

set "MODE=--onefile"
set "OUT=dist\AstrateraChronist.exe"
if /i "%~1"=="folder" (
    set "MODE=--onedir"
    set "OUT=dist\AstrateraChronist\AstrateraChronist.exe"
)

echo.
echo  === Astratera Chronist - exe build ===
echo.

rem ---- find Python -----------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 3 was not found.
    echo         Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.9 or newer is required.
    goto :fail
)

rem ---- private build environment ---------------------------------------------
if not exist ".venv-build\Scripts\python.exe" (
    echo [1/4] Creating build environment ...
    %PY% -m venv .venv-build || goto :fail
)
set "VPY=.venv-build\Scripts\python.exe"

echo [2/4] Installing requirements ...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt pyinstaller || goto :fail

rem ---- icon (only created if it is missing) -----------------------------------
if not exist "assets\chronist.ico" (
    echo       creating assets\chronist.ico ...
    if not exist assets mkdir assets
    set "QT_QPA_PLATFORM=offscreen"
    set "CHRONIST_NO_AUDIO=1"
    "%VPY%" astratera_chronist.py --export-icon assets\chronist.ico || goto :fail
    set "QT_QPA_PLATFORM="
    set "CHRONIST_NO_AUDIO="
)

echo [3/4] Building the exe - this takes a few minutes ...
"%VPY%" -m PyInstaller --noconfirm --clean %MODE% --windowed ^
    --name AstrateraChronist ^
    --icon assets\chronist.ico ^
    --exclude-module tkinter ^
    astratera_chronist.py || goto :fail

if not exist "%OUT%" (
    echo [ERROR] PyInstaller finished but %OUT% does not exist.
    goto :fail
)

echo [4/4] Done.
echo.
echo  Your program:  %CD%\%OUT%
echo.
explorer "%CD%\dist"
pause
exit /b 0

:fail
echo.
echo  *** Build failed - see the messages above. ***
echo.
pause
exit /b 1
