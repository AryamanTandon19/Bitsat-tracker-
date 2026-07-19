@echo off
REM Society AI Watchdog — run automatically every time Windows starts.
REM Double-click ONCE. To undo: delete "Society Watchdog" from shell:startup.
powershell -NoProfile -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Society Watchdog.lnk');" ^
  "$s.TargetPath='%~dp0start_watchdog.bat';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.Save()"
if %errorlevel%==0 (
    echo Done! The watchdog will now start automatically when Windows starts.
) else (
    echo Something went wrong — tell Claude the error above.
)
pause
