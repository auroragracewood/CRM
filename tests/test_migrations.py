"""Migration runner contract test.

Every other acceptance test calls `run_pending(verbose=False)` to bring
its temp DB up to current schema. That means if `run_pending` silently
becomes a no-op (say, a glob bug that skips files), every test still
passes but the DB is stuck at v0. This test pins the runner contract.

Verifies:
  1. apply_schema(schema.sql) seeds the baseline in schema_versions
  2. run_pending applies all migrations under migrations/*.sql
  3. After run_pending, expected tables for each version exist
  4. run_pending is idempotent — second call returns [] (no-op)
  5. applied_versions() reflects what's in the schema_versions table

Usage:
  python -m tests.test_migrations
"""
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_mig_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-mig"

    for mod in list(sys.modules):
        if mod.startswith("backend") or mod == "backend":
            del sys.modules[mod]

    sys.path.insert(0, str(ROOT))
    return {"tmpdir": tmpdir, "db_path": db_path}


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def run():
    print("Setting up temp DB (no schema applied yet)...")
    info = _setup_temp_db()
    print(f"  DB: {info['db_path']}")

    from backend.db import apply_schema, db
    from backend import migrations as mig

    # ---- [1/5] Baseline: schema.sql applied -> baseline in schema_versions ----
    print("\n[1/5] apply_schema(schema.sql) seeds schema_versions baseline...")
    schema_sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    apply_schema(schema_sql)
    baseline = mig.applied_versions()
    assert isinstance(baseline, set), baseline
    assert baseline, "schema_versions empty after applying baseline schema"
    base_max = max(baseline)
    print(f"  OK   baseline applied; schema_versions = {sorted(baseline)}")

    # ---- [2/5] Pending list shows un-applied migrations from migrations/*.sql ----
    print("\n[2/5] list_pending returns expected un-applied migrations...")
    pending = mig.list_pending(baseline)
    expected_files = sorted((ROOT / "migrations").glob("*.sql"))
    expected_versions = sorted(int(p.name.split("_", 1)[0])
                               for p in expected_files
                               if p.name.split("_", 1)[0].isdigit())
    pending_versions = sorted(v for v, _ in pending)
    # Every migration version not already in baseline should be pending.
    expected_pending = [v for v in expected_versions if v not in baseline]
    assert pending_versions == expected_pending, \
        f"expected pending={expected_pending}, got {pending_versions}"
    print(f"  OK   {len(pending_versions)} migrations pending: {pending_versions}")

    # ---- [3/5] run_pending applies them all + populates schema_versions ----
    print("\n[3/5] run_pending applies every pending migration...")
    applied_now = mig.run_pending(verbose=False)
    assert applied_now == pending_versions, \
        f"run_pending returned {applied_now}, expected {pending_versions}"
    after = mig.applied_versions()
    for v in pending_versions:
        assert v in after, f"version {v} missing from schema_versions after run: {after}"
    print(f"  OK   applied {applied_now}; schema_versions now = {sorted(after)}")

    # ---- [4/5] Expected tables exist after migrations ----
    # Each migration introduces specific tables. If any are missing the
    # service-layer tests would fail downstream — pin them here so the
    # failure is localized.
    print("\n[4/5] expected post-migration tables exist...")
    must_have = [
        # v0 baseline (schema.sql)
        "contacts", "companies", "interactions", "notes", "tags",
        "consent", "audit_log", "webhooks", "webhook_events",
        "users", "api_keys", "schema_versions",
        # v1: pipelines/deals/tasks/forms + FTS index
        "pipelines", "pipeline_stages", "deals", "tasks",
        "forms", "form_submissions", "search_index",
        # v2: scoring + segments + reports catalog
        "contact_scores", "segments", "segment_members",
        # v3: portals + inbound
        "portal_tokens", "inbound_endpoints", "inbound_events",
        # v4: plugins + saved_views + roles
        "plugins", "plugin_hooks", "saved_views", "roles",
    ]
    with db() as conn:
        missing = [t for t in must_have if not _table_exists(conn, t)]
    assert not missing, f"missing tables after migrations: {missing}"
    print(f"  OK   {len(must_have)} expected tables all present")

    # ---- [5/5] Idempotency: run_pending again is a no-op ----
    print("\n[5/5] run_pending again is a no-op (idempotent)...")
    second_run = mig.run_pending(verbose=False)
    assert second_run == [], \
        f"second run_pending should return [], got {second_run}"
    print(f"  OK   second run_pending returned {second_run} (no-op)")

    print("\nMIGRATION ACCEPTANCE: PASS")
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
