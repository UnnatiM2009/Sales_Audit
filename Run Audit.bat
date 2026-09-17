@echo off
REM ===========================================================
REM  Sales Process Audit  -  double-click this file to run.
REM
REM  Put the month's extracts in the "input" folder, then run.
REM  The reports open by themselves when it finishes.
REM ===========================================================
setlocal enabledelayedexpansion
title Sales Process Audit
cd /d "%~dp0"
color 0F
cls

echo.
echo   ===========================================================
echo     SALES PROCESS AUDIT
echo   ===========================================================
echo.

REM --- use the same environment START_AUDIT.bat built --------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo   Python was not found on this computer.
  echo.
  echo   Install it from https://python.org/downloads
  echo   and tick "Add Python to PATH" during setup.
  echo.
  pause
  exit /b 1
)

REM --- same folders the web app uses, so a walk done on a
REM --- phone is picked up here without exporting anything.
if not exist "output\physical" mkdir "output\physical"
set "AUDIT_PHYSICAL_DIR=%cd%\output\physical"
set "AUDIT_HISTORY_DIR=%cd%\output"

REM --- are the four required extracts present? ----------------
set MISSING=
if not exist "input\Enquiry*.xls*"   set MISSING=%MISSING% Enquiry
if not exist "input\Booking*.xls*"   set MISSING=%MISSING% Booking
if not exist "input\Test*Drive*.xls*" set MISSING=%MISSING% Test_Drive
if not exist "input\Retail*.xls*"    set MISSING=%MISSING% Retails

if not "%MISSING%"=="" (
  echo   These extracts are missing from the input folder:
  echo.
  echo      %MISSING%
  echo.
  echo   The input folder is about to open. Copy the month's
  echo   exports into it, then run this file again.
  echo.
  start "" "%~dp0input"
  pause
  exit /b 1
)

REM --- which month? ------------------------------------------
REM An audit run this month examines last month. Enter to accept.
for /f %%m in ('%PY% -c "from datetime import date;d=date.today();y,m=(d.year,d.month-1) if d.month>1 else (d.year-1,12);print(f'{y:04d}-{m:02d}')"') do set LASTMONTH=%%m
echo   Audit month?
echo.
echo      [Enter]  %LASTMONTH%  - last completed month
echo      or type another month as YYYY-MM
echo.
set MONTHOPT=
set /p MCHOICE=  Press Enter, or type a month:
if not "!MCHOICE!"=="" set MONTHOPT=--month !MCHOICE!
echo.

REM --- which branch? -----------------------------------------
echo   Audit which branch?
echo.
echo      [Enter]  All branches - the whole network
echo      [B]      One branch only
echo.
set BRANCH=
set /p CHOICE=  Press Enter, or type B then Enter:

if /i "%CHOICE%"=="B" (
  echo.
  %PY% run_audit.py --list-branches 2>nul
  echo.
  set /p BNAME=  Type the branch name exactly as listed:
  if not "!BNAME!"=="" set BRANCH=--branch "!BNAME!"
)

echo.
echo   Running. This takes about a minute...
echo   ===========================================================
echo.

%PY% run_audit.py %MONTHOPT% %BRANCH%
set RESULT=%errorlevel%

echo.
echo   ===========================================================
if %RESULT% neq 0 (
  echo   The audit did not finish. The message above says why.
  echo.
  echo   Most often it is a missing or renamed extract in the
  echo   input folder.
  echo.
  pause
  exit /b %RESULT%
)

echo   Done. Opening the reports folder.
echo   ===========================================================
timeout /t 2 >nul
start "" "%~dp0output"
exit /b 0
