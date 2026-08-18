@echo off
REM Start ImgMetaManager on Windows.
REM Creates the virtual environment and installs the dependencies on first run.
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

if not exist ".venv" (
    echo Creating the virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo Python 3.10 or newer is required. Install it from python.org, then retry.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    echo Installing dependencies ^(first run only^)...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" -m imgmetamanager %*
if errorlevel 1 pause
endlocal
