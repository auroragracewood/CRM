"""Reference deploy script for self-hosters.

This is a STARTING POINT, not a one-size-fits-all deploy. The pattern mirrors
how a single-box self-host runs (e.g., behind a tunnel to a home server):

  1. Local: git push to remote.
  2. Remote: pull, install requirements, restart the FastAPI process.

The exact commands depend on YOUR environment. Adapt this script:
- Replace REMOTE_HOST and REMOTE_PATH with your server.
- Swap out the restart mechanism (systemd, supervisord, Windows service, etc.).
- Pick a process manager that matches your OS.

Usage:
    python deploy.py                       # full deploy
    python deploy.py --skip-pull           # restart only
"""
import argparse
import os
import subprocess
import sys

REMOTE_HOST = os.environ.get("CRM_DEPLOY_HOST", "your-server")
REMOTE_PATH = os.environ.get("CRM_DEPLOY_PATH", "/srv/crm")
RESTART_CMD = os.environ.get(
    "CRM_RESTART_CMD",
    "systemctl --user restart crm",   # adjust to your process manager
)


def run(cmd: list, check: bool = True):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pull", action="store_true")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--restart-only", action="store_true")
    args = ap.parse_args()

    if args.restart_only:
        print(f"=== Restart {REMOTE_HOST}:{REMOTE_PATH} ===")
        run(["ssh", REMOTE_HOST, RESTART_CMD])
        return

    if not args.skip_pull:
        print(f"=== Git pull on {REMOTE_HOST} ===")
        run(["ssh", REMOTE_HOST, f"cd {REMOTE_PATH} && git pull --ff-only"])

    if not args.skip_install:
        print(f"=== pip install on {REMOTE_HOST} ===")
        run(["ssh", REMOTE_HOST,
             f"cd {REMOTE_PATH} && python -m pip install --upgrade -r requirements.txt"])

    print(f"=== Restart ===")
    run(["ssh", REMOTE_HOST, RESTART_CMD])

    print("\nDeploy complete.")
    print(f"  Verify: ssh {REMOTE_HOST} {RESTART_CMD.split()[0]} status crm")


if __name__ == "__main__":
    main()
