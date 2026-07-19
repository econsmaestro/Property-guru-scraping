@echo off
rem Double-click this file to open the PropertyGuru scraper in your browser.
rem The first run sets everything up automatically (takes a few minutes).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: installing what the scraper needs...
    echo This takes a few minutes. Please wait.
    python -m venv .venv || goto :nopython
    ".venv\Scripts\python" -m pip install -r requirements.txt || goto :fail
    ".venv\Scripts\python" -m playwright install chromium || goto :fail
)

rem Make sure newer additions (like Flask) are present even on old setups
".venv\Scripts\python" -m pip install -q -r requirements.txt

start "" ".venv\Scripts\pythonw.exe" webapp.py
exit /b 0

:nopython
echo.
echo Python was not found. Install it from https://www.python.org/downloads/
echo and IMPORTANT: tick "Add Python to PATH" during installation.
pause
exit /b 1

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
