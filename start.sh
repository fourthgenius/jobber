#!/usr/bin/env bash
# Mac/Linux: first run creates a venv and installs deps, then opens the dashboard.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
.venv/bin/python app.py
