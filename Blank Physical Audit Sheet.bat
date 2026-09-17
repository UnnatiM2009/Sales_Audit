@echo off
REM Writes a blank Physical Audit Sheet into the output folder.
setlocal
title Physical Audit Sheet
cd /d "%~dp0"
echo.
echo   Writing a blank Physical Audit Sheet...
echo.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% run_audit.py --make-sheet
echo.
echo   Fill it in, then save it into the input folder as
echo   Physical_Audit_Sheet.xlsx and run the audit again.
echo.
start "" "%~dp0output"
pause
