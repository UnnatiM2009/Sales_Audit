@echo off
REM Opens the folder the audit reads its extracts from.
cd /d "%~dp0"
if not exist "input" mkdir "input"
start "" "%~dp0input"
