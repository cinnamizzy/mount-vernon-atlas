@echo off
REM Start a local web server for the atlas. Double-click, or run from cmd.
REM
REM `py` and `python` on this machine resolve to the Microsoft Store stub and
REM fail with "Python was not found", so this looks for a real interpreter.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set PORT=%1
if "%PORT%"=="" set PORT=8000

set PY=
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python313\python.exe"
) do (
  if exist %%P if not defined PY set PY=%%P
)

if not defined PY (
  echo.
  echo Could not find a real Python interpreter.
  echo Looked in %%LOCALAPPDATA%%\Programs\Python\ and %%ProgramFiles%%\
  echo.
  echo If Python is installed elsewhere, run this instead:
  echo    "full\path\to\python.exe" -m http.server %PORT%
  echo.
  pause
  exit /b 1
)

echo.
echo   Mount Vernon Estate Atlas
echo   %PY%
echo.
echo   Open  http://localhost:%PORT%/
echo   Stop  Ctrl+C
echo.

start "" "http://localhost:%PORT%/"
%PY% -m http.server %PORT%
