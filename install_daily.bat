@echo off
REM Registers a Windows task that runs daily.bat every day at 08:30.
cd /d %~dp0
schtasks /Create /F /SC DAILY /ST 08:30 /TN JobHunterDaily /TR "\"%~dp0daily.bat\""
pause
