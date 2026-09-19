@echo off
REM ── IRIS ── One-command setup + launch (Windows) ───────────────────────────
REM Requires: Python 3.12+
REM
REM Usage:
REM   setup.bat          install, then show startup options
REM   setup.bat start    install + run the server in foreground
REM   setup.bat test     run the full test suite
REM ───────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [INFO] Setting up IRIS ...
echo.

REM ── Check Python ───────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12+ from https://www.python.org/downloads/
    echo        Make sure "Add Python to PATH" is checked during installation.
    pause
    exit /b 1
)
for /f "tokens=2 delims=." %%a in ('python -c "import sys; print(sys.version)"') do set PYVER=%%a
echo [OK] Python %PYVER% detected
echo.

REM ── Create venv if missing ─────────────────────────────────────────────────
if not exist ".venv\" (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Try: python -m ensurepip
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created at .venv\
) else (
    echo [OK] .venv already exists — skipping
)
echo.

REM ── Activate venv ──────────────────────────────────────────────────────────
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Could not activate venv
    pause
    exit /b 1
)

REM ── Install dependencies ───────────────────────────────────────────────────
if not exist ".venv\INSTALL_COMPLETE" (
    echo [INFO] Installing dependencies from requirements.txt...
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo [WARN] Some packages may have failed. Check output above.
    )
    touch .venv\INSTALL_COMPLETE
    echo [OK] Dependencies installed
) else (
    echo [OK] Dependencies already installed — skipping
)
echo.

REM ── Config ─────────────────────────────────────────────────────────────────
if not exist "config.json" (
    if exist "config.example.json" (
        echo [INFO] No config.json found — copying from config.example.json
        copy config.example.json config.json >nul
        echo [OK] Created config.json — edit it to set your LLM URL etc.
    ) else (
        echo [WARN] config.example.json not found — running with built-in defaults
    )
) else (
    echo [OK] config.json already present
)
echo.

REM ── Action dispatch ────────────────────────────────────────────────────────
if "%~1"=="start" goto :start_server
if "%~1"=="test"  goto :run_tests

echo.
echo ========================================
echo  IRIS Setup Complete!
echo ========================================
echo.
echo   To start the server:
echo     setup.bat start
echo.
echo   To run tests:
echo     setup.bat test
echo.
echo   Or manually:
echo     .venv\Scripts\activate
echo     uvicorn backend.main:app --host 0.0.0.0 --port 8000
echo.
pause
exit /b 0

:start_server
echo [INFO] Starting IRIS backend on http://localhost:8000 ...
echo Press Ctrl+C to stop.
echo.
uvicorn backend.main:app --host 0.0.0.0 --port 8000
goto :eof

:run_tests
echo [INFO] Running tests...
python -m pytest tests/ -q --tb=short
goto :eof
