@echo off
setlocal
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem  This file is intentionally pure ASCII.
rem
rem  A .bat containing Chinese breaks on a Chinese Windows: cmd parses
rem  the file byte-by-byte using the console code page (936), so UTF-8
rem  Chinese gets mis-decoded and swallows the following ASCII char,
rem  producing errors like "'efined' is not recognized".
rem
rem  All Chinese output and all real logic live in launcher.py instead.
rem ------------------------------------------------------------------

set "PY="

py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  for %%D in (313 312 311 310) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%D\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python%%D\python.exe"
  )
)

if not defined PY (
  for %%D in (313 312 311 310) do (
    if not defined PY if exist "C:\Program Files\Python%%D\python.exe" set PY="C:\Program Files\Python%%D\python.exe"
  )
)

if not defined PY (
  for /d %%V in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if not defined PY if exist "%%V\python.exe" set PY="%%V\python.exe"
  )
)

if not defined PY (
  echo.
  echo   Python not found.
  echo.
  echo   Install Python 3.10 or newer from python.org and tick
  echo   "Add python.exe to PATH" during setup.
  echo.
  echo   Do NOT use the Microsoft Store version - it is a stub
  echo   that fails with a non-zero exit code and looks like a bug.
  echo.
  pause
  exit /b 1
)

%PY% launcher.py %*
if errorlevel 1 pause
exit /b 0
