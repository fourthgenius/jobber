@echo off
REM Windows: first run creates a venv and installs deps, then opens the dashboard.
cd /d %~dp0
if not exist .venv ( python -m venv .venv && .venv\Scripts\pip install -q -r requirements.txt )
.venv\Scripts\python app.py
