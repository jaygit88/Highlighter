@echo off
rem Double-click this file to start Highlighter on Windows.
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (
  echo ffmpeg is not installed. Open PowerShell and run:  winget install Gyan.FFmpeg
  echo Then close this window and double-click run.bat again.
  pause
  exit /b 1
)
if not exist .venv (
  echo First run: setting things up, this takes a minute...
  python -m venv .venv || (echo Python is not installed. Get it from https://www.python.org/downloads/ & pause & exit /b 1)
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python app.py
pause
