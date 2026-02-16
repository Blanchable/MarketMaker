@echo off
setlocal
title Kalshi BTC Bot - Setup and Launch

:: Change to the directory where this BAT file lives
cd /d "%~dp0"

echo.
echo   ================================================================
echo     Kalshi BTC Bot - One-Click Setup
echo   ================================================================
echo.

:: ── Check for Python ────────────────────────────────────────────────
:: Try "python" first (standard Windows install), then "python3", then "py"
set PYTHON_CMD=
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    goto :check_version
)
where python3 >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python3
    goto :check_version
)
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=py
    goto :check_version
)

echo   [ERROR] Python is not installed or not on your PATH.
echo.
echo   Please install Python 3.11 or newer from:
echo     https://www.python.org/downloads/
echo.
echo   IMPORTANT: During install, check "Add Python to PATH".
echo.
pause
exit /b 1

:check_version
:: Verify Python version is 3.11+
for /f "tokens=2 delims= " %%v in ('%PYTHON_CMD% --version 2^>^&1') do set PYVER=%%v
echo   Found Python %PYVER%
echo.

:: ── Run the bootstrapper ────────────────────────────────────────────
%PYTHON_CMD% one_click_setup_and_run.py
set EXIT_CODE=%ERRORLEVEL%

:: ── Keep window open on error ───────────────────────────────────────
if %EXIT_CODE% neq 0 (
    echo.
    echo   Setup exited with an error. See messages above.
    echo.
    pause
)

exit /b %EXIT_CODE%
