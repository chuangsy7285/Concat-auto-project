@echo off
cd /d "%~dp0"
py -3 concat_auto.py
if errorlevel 1 echo Please read the error message above.
pause
