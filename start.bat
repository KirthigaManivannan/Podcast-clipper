@echo off
rem ==========================================================================
rem  PodClip launcher (Windows)
rem  First run: creates .venv, installs dependencies, copies .env from
rem  .env.example, checks your OpenRouter key, then starts the app.
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
set "FAILED="

rem ---- pick a Python 3.10-3.13 (3.12 preferred over any 3.14+) ----
set "PY="
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3.12"
if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [podclip] Python was not found. Install Python 3.10-3.13 from python.org,
    echo          tick "Add to PATH", then run start.bat again.
    pause
    goto :eof
)

rem ---- .env from example ----
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [podclip] Created .env from .env.example.
)

rem ---- early key check (stdlib only) ----
%PY% check_env.py
if errorlevel 1 (
    echo.
    echo [podclip] Open .env and paste your key after OPENROUTER_API_KEY=
    echo          Save the file, then run start.bat again.
    pause
    goto :eof
)

rem ---- venv ----
if not exist ".venv\Scripts\python.exe" (
    echo [podclip] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 set "FAILED=1"
)
if not defined FAILED (
    echo [podclip] Installing dependencies (first run takes a few minutes)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 set "FAILED=1"
)
if defined FAILED (
    echo [podclip] Dependency installation failed. Check the output above and your
    echo          ^(Python 3.10-3.13^) install, then run start.bat again.
    pause
    goto :eof
)

echo [podclip] Starting PodClip... opening the browser shortly.
start "" "http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app.py
pause
endlocal
