@echo off
REM CRM dev launcher.
REM First time: run `python setup.py` to create the database + admin user.
REM Then run this file (or `python server.py`).

setlocal
cd /d "%~dp0"

if not exist crm.db (
  echo crm.db not found — running setup.py first.
  python setup.py
  if errorlevel 1 exit /b %errorlevel%
)

echo Starting CRM on http://127.0.0.1:8765/
python server.py
