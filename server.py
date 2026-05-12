"""Entry point. Runs FastAPI via uvicorn.

Usage:
  python server.py                 # serves on http://127.0.0.1:8765
  CRM_PORT=9000 python server.py   # custom port

For dev use only. Production self-hosters should fork deploy.py.
"""
import os
import sys

import uvicorn

from backend import migrations as migrations_runner
from backend.db import schema_initialized


def main():
    if not schema_initialized():
        print("CRM database is not initialized.")
        print("Run `python setup.py` first to create the database and an admin user.")
        sys.exit(1)

    # Apply any pending migrations before serving. Idempotent — re-running a
    # migration that's already recorded in schema_versions does nothing.
    ran = migrations_runner.run_pending(verbose=True)
    if ran:
        print(f"Applied {len(ran)} pending migration(s) on startup: {ran}")

    port = int(os.environ.get("CRM_PORT", "8765"))
    host = os.environ.get("CRM_HOST", "127.0.0.1")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
