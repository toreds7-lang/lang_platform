@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYEXE="

py -3 --version >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"

if not defined PYEXE (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)

if not defined PYEXE (
    echo Python was not found.
    echo Please install Python 3.10+ from https://www.python.org/downloads/ and make sure to check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment...
    !PYEXE! -m venv .venv
)

call ".venv\Scripts\activate.bat"

REM The stamp holds requirements.txt's timestamp rather than just existing, so
REM that adding a dependency actually reinstalls. A bare "have we ever
REM installed?" flag silently skips new packages and the app then fails at
REM runtime with an import error.
set "STAMP=.venv\.installed"
for %%A in ("requirements.txt") do set "REQ_TIME=%%~tA"

set "NEED_INSTALL=1"
if exist "%STAMP%" (
    set /p SAVED_TIME=<"%STAMP%"
    if "!SAVED_TIME!"=="!REQ_TIME!" set "NEED_INSTALL="
)

if defined NEED_INSTALL (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
    echo !REQ_TIME!> "%STAMP%"
)

echo Starting server...
python server\main.py

pause
