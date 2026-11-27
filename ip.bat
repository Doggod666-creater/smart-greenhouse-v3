@echo off
setlocal
cd /d "%~dp0"

rem  Reuses the Python discovery in start.bat, then asks launcher.py
rem  for today's LAN address. No hard-coded interpreter path on purpose:
rem  that breaks the moment this folder moves to another machine.

call "%~dp0start.bat" --ip

echo.
pause
exit /b 0
