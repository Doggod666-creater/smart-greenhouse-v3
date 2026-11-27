@echo off
setlocal
cd /d "%~dp0"

rem  Pure ASCII on purpose - see the note in start.bat.

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
  echo   Install Python 3.10+ from python.org with "Add to PATH" ticked.
  echo.
  pause
  exit /b 1
)

%PY% -c "import pytest" >nul 2>&1
if errorlevel 1 (
  echo   Installing pytest...
  %PY% -m pip install pytest --index-url https://pypi.org/simple --disable-pip-version-check
)

echo.
echo   Interpreter: %PY%
echo.
%PY% -m pytest tests -v

echo.
echo   All green means the six rules, the pump guard and the API
echo   contract still hold. Run this again after touching rules.py.
echo.
pause
exit /b 0
