@echo off
rem ===========================================================================
rem  Sales Process Audit — start the app on this laptop.
rem
rem  Double-click this file. The first run takes a minute or two while it sets
rem  itself up; after that it starts in a few seconds.
rem
rem  Leave the black window open while you are working. Closing it stops the
rem  app. Nothing is uploaded anywhere — everything stays on this machine.
rem ===========================================================================
setlocal
cd /d "%~dp0"
title Sales Process Audit — running (do not close)

echo.
echo   Sales Process Audit
echo   -------------------
echo.

rem --- find Python -----------------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY goto :nopython

rem --- set up a private environment on first run -----------------------------
if not exist ".venv\Scripts\python.exe" (
    echo   First run — setting up. This takes a minute or two.
    echo.
    %PY% -m venv .venv
    if errorlevel 1 goto :venvfailed
)

set "VPY=.venv\Scripts\python.exe"

rem --- install or update the libraries it needs ------------------------------
echo   Checking libraries...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :pipfailed

rem --- keep the phone captures and the trend next to the project ------------
if not exist "output\physical" mkdir "output\physical"
set "AUDIT_PHYSICAL_DIR=%cd%\output\physical"
set "AUDIT_HISTORY_DIR=%cd%\output"

rem --- go ---------------------------------------------------------------------
echo.
"%VPY%" webapp\app.py

echo.
echo   The app has stopped.
pause
exit /b 0

:nopython
echo   Python is not installed on this machine, or it is not on the PATH.
echo.
echo   Install it from https://www.python.org/downloads/  and tick
echo   "Add python.exe to PATH" on the first screen of the installer.
echo   Then double-click this file again.
echo.
pause
exit /b 1

:venvfailed
echo.
echo   Could not create the environment. Try running this file from a folder
echo   that is not inside OneDrive or a synced drive.
echo.
pause
exit /b 1

:pipfailed
echo.
echo   The libraries could not be installed. Check that this machine can reach
echo   the internet, then try again. If your office blocks pypi.org, ask IT to
echo   allow it, or install the packages listed in requirements.txt manually.
echo.
pause
exit /b 1
