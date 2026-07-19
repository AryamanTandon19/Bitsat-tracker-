@echo off
REM Society AI Watchdog — start (and auto-restart on crash).
REM Double-click this file to run the app. Close the window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
    echo Workspace missing — creating it now...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
:loop
echo.
echo ================ Society AI Watchdog starting ================
python -m app.main
echo.
echo App stopped or crashed — restarting in 5 seconds (close window to quit)
timeout /t 5 /nobreak >nul
goto loop
