@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 echo Installation failed. Check Python and internet connection.
pause
