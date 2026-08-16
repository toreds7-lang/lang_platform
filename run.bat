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

if not exist ".venv\.installed" (
    echo Installing dependencies, this only happens once...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
    echo. > ".venv\.installed"
)

echo Starting server...
python server\main.py

pause
