@echo off
REM Society AI Watchdog — ONE-CLICK UPDATE.
REM Downloads the latest code and replaces ONLY the program files.
REM Your settings (config.yaml), API key, database, clips and test videos are
REM NOT touched. New default settings are saved as config.new.yaml for review.
setlocal
cd /d "%~dp0"
set URL=https://github.com/AryamanTandon19/Bitsat-tracker-/archive/refs/heads/claude/society-ai-watchdog-demo-hxshu9.zip
set TMPD=%TEMP%\watchdog_update

echo [1/4] Downloading latest code...
curl -L -s -o "%TEMP%\watchdog_update.zip" "%URL%"
if errorlevel 1 goto fail

echo [2/4] Unpacking...
rmdir /s /q "%TMPD%" 2>nul
mkdir "%TMPD%"
tar -xf "%TEMP%\watchdog_update.zip" -C "%TMPD%"
if errorlevel 1 goto fail
for /d %%D in ("%TMPD%\*") do set SRC=%%D

echo [3/4] Updating program files...
robocopy "%SRC%\app" app /MIR /NFL /NDL /NJH /NJS >nul
copy /y "%SRC%\validate_triggers.py" . >nul
copy /y "%SRC%\verify_audit.py" . >nul
copy /y "%SRC%\zones.py" . >nul
copy /y "%SRC%\discover.py" . >nul
copy /y "%SRC%\requirements.txt" . >nul
copy /y "%SRC%\tests\make_sample_video.py" tests\ >nul 2>nul
copy /y "%SRC%\config.yaml" config.new.yaml >nul

echo [4/4] Installing any new parts...
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    pip install -q -r requirements.txt
)

echo.
echo ================= UPDATE COMPLETE =================
echo Your settings were NOT changed (config.yaml kept as-is).
echo Latest default settings saved as: config.new.yaml
echo Now start the app with: start_watchdog.bat
pause
exit /b 0

:fail
echo.
echo UPDATE FAILED — check your internet connection and try again.
pause
exit /b 1
