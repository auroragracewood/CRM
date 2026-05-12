"""First-run wizard.

Creates the database, applies schema.sql, prompts for the first admin
user's email + password, generates one API key (shown ONCE), and prints
next-step instructions.

Usage:
  python setup.py                  # interactive
  python setup.py --non-interactive \
      --admin-email a@b.c --admin-password ... [--key-name ...]
"""
import argparse
import getpass
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend import auth as auth_mod  # noqa: E402
from backend import migrations as migrations_runner  # noqa: E402
from backend.db import DB_PATH, applied_versions, apply_schema, db  # noqa: E402


SCHEMA_FILE = ROOT / "schema.sql"


def banner(text: str):
    print()
    print("=" * 64)
    print(f"  {text}")
    print("=" * 64)


def parse_args():
    ap = argparse.ArgumentParser(description="CRM first-run setup")
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-password")
    ap.add_argument("--admin-name", default="Admin")
    ap.add_argument("--key-name", default="setup-default")
    ap.add_argument("--force", action="store_true",
                    help="Apply schema even if already initialized")
    return ap.parse_args()


def main():
    args = parse_args()
    banner("CRM — first-run setup")
    print(f"Database file: {DB_PATH}")

    already = applied_versions()
    if already and not args.force:
        print(f"Database already initialized (schema versions: {already}).")
        print("Use --force to re-apply (will INSERT a duplicate schema_versions row).")
        sys.exit(0)

    # 1. Apply schema
    if not SCHEMA_FILE.exists():
        print(f"ERROR: {SCHEMA_FILE} not found")
        sys.exit(1)
    schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
    print("Applying schema.sql ...")
    apply_schema(schema_sql)
    print(f"  ok, versions now: {applied_versions()}")

    # 1b. Apply pending migrations (v1 → v4.1, and any future ones)
    print("Applying pending migrations ...")
    ran = migrations_runner.run_pending(verbose=True)
    if ran:
        print(f"  applied {len(ran)} migration(s): {ran}")
    else:
        print("  no pending migrations")

    # 2. Collect admin credentials
    if args.non_interactive:
        admin_email = args.admin_email
        admin_pw = args.admin_password
        admin_name = args.admin_name
        if not admin_email or not admin_pw:
            print("ERROR: --admin-email and --admin-password required in non-interactive mode")
            sys.exit(2)
    else:
        print()
        print("Create the first admin user:")
        admin_email = input("  email:    ").strip().lower()
        admin_name = input("  display name (optional): ").strip() or "Admin"
        while True:
            pw1 = getpass.getpass("  password: ")
            pw2 = getpass.getpass("  confirm:  ")
            if pw1 and pw1 == pw2:
                admin_pw = pw1
                break
            print("  passwords didn't match (or were empty), try again")

    if "@" not in admin_email:
        print("ERROR: admin email is not a valid address")
        sys.exit(3)
    if len(admin_pw) < 8:
        print("WARNING: password is shorter than 8 characters; consider strengthening.")

    pw_hash = auth_mod.hash_password(admin_pw)
    now = int(time.time())

    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
        if existing:
            print(f"  user {admin_email!r} already exists (id={existing[0]})")
            user_id = existing[0]
        else:
            conn.execute(
                "INSERT INTO users (email, password_hash, display_name, role, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (admin_email, pw_hash, admin_name, "admin", now, now),
            )
            user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            print(f"  created admin user id={user_id}")

    # 3. Generate one API key, shown once.
    raw, prefix, key_hash = auth_mod.generate_api_key()
    with db() as conn:
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, args.key_name, prefix, key_hash, "admin", now),
        )
        key_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    banner("Setup complete")
    print(f"  Admin user:  {admin_email}  (id={user_id})")
    print(f"  API key:     {raw}")
    print(f"  Key id:      {key_id} (scope=admin, name={args.key_name!r})")
    print()
    print("  *** Copy the API key NOW. It will not be shown again. ***")
    print()
    print("Next steps:")
    print("  1. Save the API key somewhere safe (it's only printed here).")
    print("  2. Run:  python server.py   (or start.bat)")
    print("  3. Browse: http://127.0.0.1:8765/   and sign in with the email/password above.")
    print()


if __name__ == "__main__":
    main()
