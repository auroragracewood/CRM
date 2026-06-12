"""Remaining-services coverage script.

Picks up where test_v1plus.py left off — exercises the services that have
no other acceptance coverage:

  - auth_keys (API key lifecycle)
  - users (admin creates additional users)
  - roles (custom role + permission grant)
  - search (FTS5 cross-entity)
  - duplicates (email-collision strategy)
  - imports (CSV → contacts)
  - inbound (endpoint + HMAC-signed receive)
  - portals (issue token → resolve)
  - reports (sample report renders)

Service-layer only — transport coverage is already in test_milestone1.

Usage:
  python -m tests.test_services_coverage
"""
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_cov_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-cov"

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
            ("admin@cov.test", pw_hash, "Cov Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"tmpdir": tmpdir, "db_path": db_path, "user_id": user_id}


def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:    {info['db_path']}")
    print(f"  Admin: id={info['user_id']}")

    from backend.context import ServiceContext
    from backend.db import db
    from backend.services import (
        contacts as contacts_svc,
        auth_keys as auth_keys_svc,
        users as users_svc,
        roles as roles_svc,
        search as search_svc,
        duplicates as duplicates_svc,
        imports as imports_svc,
        inbound as inbound_svc,
        portals as portals_svc,
        reports as reports_svc,
    )

    ctx = ServiceContext(user_id=info["user_id"], role="admin",
                         scope="admin", surface="cli")

    # ---- [1/9] AUTH_KEYS lifecycle (create -> list -> revoke) ----
    print("\n[1/9] auth_keys.create + list_for_user + revoke...")
    key = auth_keys_svc.create(ctx, info["user_id"], "ci-bot", scope="write")
    assert key["raw_key"] and key["key_prefix"], key
    keys_list = auth_keys_svc.list_for_user(ctx, info["user_id"])
    assert any(k["id"] == key["id"] for k in keys_list), keys_list
    revoked = auth_keys_svc.revoke(ctx, key["id"])
    assert revoked, revoked
    print(f"  OK   API key {key['key_prefix']}*** created -> listed -> revoked")

    # ---- [2/9] USERS (admin creates a second account) ----
    print("\n[2/9] users.create_user + list_...")
    u2 = users_svc.create_user(
        ctx, email="teammate@cov.test", password="another-strong-pw",
        display_name="Team Mate", role="user",
    )
    assert u2["id"] and u2["email"] == "teammate@cov.test", u2
    user_list = users_svc.list_(ctx)
    assert any(u["id"] == u2["id"] for u in user_list), user_list
    print(f"  OK   user id={u2['id']} role={u2['role']} (registry has {len(user_list)} users)")

    # ---- [3/9] ROLES (custom role + permission grant) ----
    print("\n[3/9] roles.create + grant_permission...")
    role = roles_svc.create(ctx, "campaign-manager",
                            description="manages outbound campaigns")
    assert role["id"] and role["name"] == "campaign-manager", role
    granted = roles_svc.grant_permission(ctx, role["id"], "deals.write")
    assert granted, granted
    role_after = roles_svc.get(ctx, role["id"])
    assert "deals.write" in role_after.get("permissions", []), role_after
    print(f"  OK   role id={role['id']} now has permissions: {role_after['permissions']}")

    # ---- [4/9] SEARCH (FTS5 cross-entity) ----
    # Search requires data the FTS index actually mirrors. Per migration
    # 0003_fts5.sql the contact body is `email + title + location` (NOT
    # `about`), so the query has to hit one of those — `title` is the
    # cleanest, it's a single distinctive token.
    print("\n[4/9] search.search (FTS5)...")
    contacts_svc.create(ctx, {
        "full_name": "Searchable Sarah",
        "email": "sarah@cov.test",
        "title": "Cobaltsmith",  # unique token guaranteed to be in the FTS body
    })
    hits = search_svc.search(ctx, "Cobaltsmith")
    found = hits.get("total", 0) > 0 or len(hits.get("items") or []) > 0
    assert found, f"FTS search for 'Cobaltsmith' returned no hits: {hits}"
    print(f"  OK   'Cobaltsmith' search -> {hits.get('total', len(hits['items']))} hit(s)")

    # ---- [5/9] DUPLICATES (email-collision strategy) ----
    print("\n[5/9] duplicates.find (phone strategy)...")
    # Email has a partial UNIQUE index on active contacts (schema.sql:
    # uq_contacts_active_email), so email dupes are only possible across
    # active+soft-deleted. Phone has no such constraint — cleaner to exercise.
    contacts_svc.create(ctx, {
        "full_name": "Dup One",
        "email": "dup1@cov.test", "phone": "555-0100",
    })
    contacts_svc.create(ctx, {
        "full_name": "Dup Two",
        "email": "dup2@cov.test", "phone": "(555) 0100",  # different format, same digits
    })
    dup_report = duplicates_svc.find(ctx, strategies=["phone"])
    assert dup_report["total_groups"] >= 1, dup_report
    phone_group = next(
        (g for g in dup_report["groups"] if g["strategy"] == "phone"),
        None,
    )
    assert phone_group and len(phone_group["contacts"]) == 2, phone_group
    print(f"  OK   duplicate group on key={phone_group['key']!r} "
          f"size={len(phone_group['contacts'])} "
          f"(proves phone normalization across formats)")

    # ---- [6/9] IMPORTS (CSV -> contacts) ----
    print("\n[6/9] imports.import_contacts (CSV)...")
    csv_text = (
        "full_name,email,phone\n"
        "Imported Ivy,ivy@cov.test,555-0100\n"
        "Imported Jay,jay@cov.test,555-0101\n"
    )
    import_result = imports_svc.import_contacts(ctx, csv_text)
    assert import_result.get("created", 0) >= 2, import_result
    print(f"  OK   imported {import_result['created']} contact(s); "
          f"matched={import_result.get('matched', 0)} errors={len(import_result.get('errors', []))}")

    # ---- [7/9] INBOUND (endpoint + HMAC-signed receive) ----
    print("\n[7/9] inbound.create_endpoint + receive (HMAC-signed)...")
    ep = inbound_svc.create_endpoint(
        ctx, slug="zapier-leads", name="Zapier leads",
        routing={"contact_email_path": "email", "contact_name_path": "name"},
    )
    assert ep["id"] and ep["shared_secret"], ep
    payload = json.dumps({
        "name": "Inbound Iris", "email": "iris.inbound@cov.test",
    }).encode("utf-8")
    sig = hmac.new(ep["shared_secret"].encode(), payload, hashlib.sha256).hexdigest()
    result = inbound_svc.receive(
        "zapier-leads", payload,
        headers={"X-CRM-Inbound-Signature": sig,
                 "Content-Type": "application/json"},
        ip="127.0.0.1", user_agent="cov-test/1.0",
    )
    # Status varies based on routing config ("received"/"parsed"/"matched"/
    # "created"); the load-bearing assertion is event_id + sig_valid below.
    assert result.get("event_id") and result.get("signature_valid"), result
    # Verify the raw event landed in inbound_events with signature_valid=1
    with db() as conn:
        ev = conn.execute(
            "SELECT signature_valid, status FROM inbound_events WHERE id=?",
            (result["event_id"],),
        ).fetchone()
    assert ev and ev["signature_valid"] == 1, f"signature should validate: {ev}"
    print(f"  OK   inbound event id={result['event_id']} signature_valid=1 status={ev['status']}")

    # ---- [8/9] PORTALS (issue token -> resolve) ----
    print("\n[8/9] portals.issue + resolve...")
    portal_contact = contacts_svc.create(ctx, {
        "full_name": "Portal Pat", "email": "pat.portal@cov.test",
    })
    portal = portals_svc.issue(
        ctx, portal_contact["id"], scope="client",
        label="self-service link", expires_in_days=30,
    )
    assert portal["token"] and portal["contact_id"] == portal_contact["id"], portal
    resolved = portals_svc.resolve(portal["token"])
    assert resolved and resolved["contact"]["id"] == portal_contact["id"], resolved
    print(f"  OK   portal token issued (len={len(portal['token'])}) -> "
          f"resolved to contact id={resolved['contact']['id']} scope={resolved['scope']}")

    # ---- [9/9] REPORTS (sample reports render without error) ----
    # Reports are read-only aggregations. Exercising two of the seven gets us
    # confidence the catalog is wired; per-report logic is the reports module's
    # job to keep correct, not this acceptance test's job to fully audit.
    print("\n[9/9] reports.deal_pipeline_summary + tag_distribution...")
    summary = reports_svc.deal_pipeline_summary(ctx)
    assert "rows" in summary and "columns" in summary, summary
    tag_dist = reports_svc.tag_distribution(ctx)
    assert "rows" in tag_dist and "columns" in tag_dist, tag_dist
    print(f"  OK   deal_pipeline_summary: {len(summary['rows'])} row(s); "
          f"tag_distribution: {len(tag_dist['rows'])} row(s)")

    # ---- audit_log spot-check ----
    print("\n[audit] cross-service audit_log spot-check...")
    expected = {
        "user.created", "role.created",
        "inbound_endpoint.created", "portal_token.issued",
    }
    with db() as conn:
        rows = conn.execute("SELECT DISTINCT action FROM audit_log").fetchall()
    actions = {r[0] for r in rows}
    missing = expected - actions
    assert not missing, f"audit_log missing: {missing}"
    print(f"  OK   audit_log has {len(actions)} distinct actions covering coverage services")

    print("\nSERVICES COVERAGE ACCEPTANCE: PASS")
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
