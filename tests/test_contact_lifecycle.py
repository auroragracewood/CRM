"""Contact lifecycle acceptance script.

The Blueprint promises "audit and recovery from day one" — every contact
mutation is undoable, soft-deleted records preserve their data, and
restoration rejoins them to the live set IF nothing else has claimed
their identifiers in the meantime.

The mechanism that makes this work is a *partial* unique index:
  CREATE UNIQUE INDEX uq_contacts_active_email
    ON contacts (email) WHERE email IS NOT NULL AND deleted_at IS NULL;

That partial predicate is the entire reason a soft-deleted contact frees
up its email for re-use. If the index ever drops the `WHERE` clause,
soft-delete starts failing in ways that look like data corruption.

This test pins the choreography in place:
  1. create A with email X
  2. soft-delete A  (X is now reusable)
  3. create B with email X  (proves the partial index works)
  4. try to restore A  -> CONTACT_EMAIL_EXISTS (collision check in restore)
  5. soft-delete B  (X is free again)
  6. restore A  -> succeeds (audit row + on_contact_restored hook + webhook)
  7. re-restore A -> CONTACT_NOT_FOUND (it's already live, not deleted)

If any of these stop holding, recovery is silently broken.

Usage:
  python -m tests.test_contact_lifecycle
"""
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_lc_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-lc"

    for mod in list(sys.modules):
        if mod.startswith("backend") or mod == "backend":
            del sys.modules[mod]

    sys.path.insert(0, str(ROOT))
    from backend import auth as auth_mod
    from backend.db import apply_schema, db
    from backend.migrations import run_pending as run_migrations

    schema_sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    apply_schema(schema_sql)
    run_migrations(verbose=False)

    now = int(time.time())
    pw_hash = auth_mod.hash_password("test-password-1234")
    with db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("admin@lc.test", pw_hash, "LC Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"tmpdir": tmpdir, "db_path": db_path, "user_id": user_id}


def _assert_raises(expected_code, fn, *args, **kwargs):
    from backend.services.contacts import ServiceError
    try:
        fn(*args, **kwargs)
    except ServiceError as e:
        if e.code != expected_code:
            raise AssertionError(
                f"expected ServiceError({expected_code!r}), got {e.code!r}: {e.message}")
        return e
    raise AssertionError(f"expected ServiceError({expected_code!r}), no exception raised")


def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:    {info['db_path']}")
    print(f"  Admin: id={info['user_id']}")

    from backend.context import ServiceContext
    from backend.db import db
    from backend.services import contacts as contacts_svc

    ctx = ServiceContext(user_id=info["user_id"], role="admin",
                         scope="admin", surface="cli")
    EMAIL = "reusable@lc.test"

    # ---- [1/7] CREATE A ----
    print("\n[1/7] create contact A (email=reusable@lc.test)...")
    a = contacts_svc.create(ctx, {"full_name": "Alice A", "email": EMAIL})
    assert a["id"] and a["email"] == EMAIL, a
    print(f"  OK   contact A id={a['id']}")

    # ---- [2/7] SOFT-DELETE A ----
    print("\n[2/7] soft-delete A (email becomes reusable)...")
    contacts_svc.delete(ctx, a["id"])
    with db() as conn:
        row = conn.execute(
            "SELECT id, email, deleted_at FROM contacts WHERE id=?", (a["id"],),
        ).fetchone()
    assert row["deleted_at"] is not None, row
    # Email is still on the row — soft-delete preserves data.
    assert row["email"] == EMAIL, row
    print(f"  OK   A.deleted_at set; email '{row['email']}' preserved on row")

    # ---- [3/7] CREATE B with same email (partial index allows) ----
    print("\n[3/7] create contact B with same email (partial unique index)...")
    b = contacts_svc.create(ctx, {"full_name": "Bob B", "email": EMAIL})
    assert b["id"] != a["id"] and b["email"] == EMAIL, b
    print(f"  OK   contact B id={b['id']} created with same email "
          f"(proves uq_contacts_active_email is partial)")

    # ---- [4/7] RESTORE A SHOULD FAIL (B has the email now) ----
    print("\n[4/7] restore A while B holds the email -> CONTACT_EMAIL_EXISTS...")
    err = _assert_raises("CONTACT_EMAIL_EXISTS",
                         contacts_svc.restore, ctx, a["id"])
    assert err.details.get("contact_id") == b["id"], err.details
    print(f"  OK   restore blocked; collision details point to B (id={b['id']})")

    # ---- [5/7] SOFT-DELETE B (email free again) ----
    print("\n[5/7] soft-delete B to free up the email...")
    contacts_svc.delete(ctx, b["id"])

    # ---- [6/7] RESTORE A SUCCEEDS ----
    print("\n[6/7] restore A -> success + audit + on_contact_restored hook...")
    restored = contacts_svc.restore(ctx, a["id"])
    assert restored["id"] == a["id"] and restored["email"] == EMAIL, restored
    assert restored.get("deleted_at") is None, restored
    # Verify audit log and webhook outbox.
    with db() as conn:
        actions = {r[0] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log WHERE object_id=? AND object_type='contact'",
            (a["id"],),
        ).fetchall()}
        wh_events = {r[0] for r in conn.execute(
            "SELECT DISTINCT event_type FROM webhook_events"
        ).fetchall()}
    assert "contact.created" in actions and \
           "contact.deleted" in actions and \
           "contact.restored" in actions, \
           f"audit_log missing one of created/deleted/restored: {actions}"
    print(f"  OK   A.deleted_at cleared; audit actions for this id: {sorted(actions)}")
    print(f"  OK   webhook outbox has event_types: {sorted(wh_events)}")

    # ---- [7/7] RE-RESTORE A SHOULD 404 (no longer in deleted set) ----
    print("\n[7/7] re-restore A while A is already live -> CONTACT_NOT_FOUND...")
    _assert_raises("CONTACT_NOT_FOUND", contacts_svc.restore, ctx, a["id"])
    print(f"  OK   restore-of-live-contact correctly raises CONTACT_NOT_FOUND")

    print("\nCONTACT LIFECYCLE ACCEPTANCE: PASS")
    print(f"\n(temp DB left at {info['db_path']} — safe to delete)")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
