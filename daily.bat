@echo off
REM Daily: fetch new jobs, then send the Telegram digest. Scheduled by install_daily.bat or the Copilot page.
cd /d %~dp0
.venv\Scripts\python alerts.py --fetch >> daily.log 2>&1
