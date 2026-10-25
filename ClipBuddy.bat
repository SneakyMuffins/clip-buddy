@echo off
setlocal
cd /d "%~dp0"
title Clip Buddy
if exist ".venv\Scripts\python.exe" goto run

echo First start: setting up Clip Buddy. This happens once and takes a few minutes.
echo.

rem --- Python 3.10+ (the "python" Store shortcut does not count) ---
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
set "RESTART="
if not defined PY (
  echo Installing Python...
  winget install -e --id Python.Python.3.13 --scope user
  set "RESTART=1"
)

rem --- ffmpeg (does all the video work) ---
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo Installing ffmpeg...
  winget install -e --id Gyan.FFmpeg
  set "RESTART=1"
)

if defined RESTART (
  echo.
  echo Installed. Close this window and double-click "ClipBuddy.bat" again to finish setup.
  pause
  exit /b
)

echo Installing Clip Buddy's Python packages...
"%PY%" -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto fail
echo Setup done.
echo.

:run
".venv\Scripts\python.exe" -m clipbuddy
pause
exit /b

:fail
echo.
echo Setup failed. Please open an issue with a screenshot of this window: https://github.com/SneakyMuffins/clip-buddy/issues
if exist .venv rmdir /s /q .venv
pause
