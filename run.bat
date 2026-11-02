@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
)
rem Reinstall dependencies whenever requirements.txt changes.
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail
    copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)
rem Native window by default, started without a console. Pass --browser or --server for other modes.
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m pyhttp
) else (
    ".venv\Scripts\python.exe" -m pyhttp %*
)
goto :eof
:fail
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
