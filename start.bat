@echo off
setlocal
cd /d "%~dp0"

REM --- find python ---
set PYEXE=
where python >nul 2>nul && set PYEXE=python
if not defined PYEXE (where py >nul 2>nul && set PYEXE=py)
if not defined PYEXE (
    echo [ERROR] Python 3.9+ is required and was not found on PATH.
    echo Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
    pause & exit /b 1
)

REM --- create venv once ---
if not exist venv\Scripts\python.exe (
    echo [setup] Creating virtual environment...
    %PYEXE% -m venv venv || (echo venv creation failed. & pause & exit /b 1)
)
set VPY=%CD%\venv\Scripts\python.exe

REM --- install deps once ---
%VPY% -m pip show fastapi >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing dependencies first run only...
    %VPY% -m pip install --upgrade pip
    %VPY% -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)
)

echo.
echo  Qwen Local Agent starting at http://127.0.0.1:8765
echo  Keep this window open to see logs. Close it to stop the agent.
echo.
start "" http://127.0.0.1:8765
"%VPY%" server.py
pause