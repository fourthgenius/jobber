#!/usr/bin/env bash
# Mac/Linux daily run: fetch new jobs, then send the Telegram digest (if set up).
# Add to cron with:  crontab -e   then:  30 8 * * * /path/to/job-hunter/daily.sh
cd "$(dirname "$0")"
.venv/bin/python alerts.py --fetch >> daily.log 2>&1
