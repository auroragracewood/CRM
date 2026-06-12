"""Error-path acceptance script.

The happy-path tests (milestone1, v1plus, services_coverage) prove the
services WORK when given valid input. This file proves they FAIL CORRECTLY
when given bad input — i.e. raise the documented ServiceError code, not
a generic Exception, not silently corrupt state.

For each service, exercises the three error families that matter most:
  - VALIDATION_ERROR  (bad input rejected)
  - <DOMAIN>_NOT_FOUND  (missing referent rejected)
  - FORBIDDEN  (scope/role boundary enforced)
  - <UNIQUE>_EXISTS  (uniqueness enforced)

If any of these stop raising the documented code, downstream callers
(REST handlers in particular, which map `e.code` to HTTP status) silently
break. That's why error codes need their own coverage.

Usage:
  python -m tests.test_error_paths
"""
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_err_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-err"

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
            ("admin@err.test", pw_hash, "Err Admin", "admin", now, now),
        )
        admin_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Second user (non-admin) for cross-user FORBIDDEN tests.
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("user@err.test", pw_hash, "Err User", "user", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"tmpdir": tmpdir, "db_path": db_path,
            "admin_id": admin_id, "user_id": user_id}


def _assert_raises(expected_code, fn, *args, **kwargs):
    """Run fn(...) and assert it raises ServiceError with the expected code."""
    from backend.services.contacts import ServiceError  # canonical import path
    try:
        fn(*args, **kwargs)
    except ServiceError as e:
        if e.code != expected_code:
            raise AssertionError(
                f"expected ServiceError({expected_code!r}), "
                f"got {e.code!r}: {e.message}"
            )
        return e
    raise AssertionError(
        f"expected ServiceError({expected_code!r}), no exception raised"
    )


def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:     {info['db_path']}")
    print(f"  Admin:  id={info['admin_id']}  User: id={info['user_id']}")

    from backend.context import ServiceContext
    from backend.db import db
    from backend.services import (
        contacts as contacts_svc,
        pipelines as pipelines_svc,
        deals as deals_svc,
        tasks as tasks_svc,
        forms as forms_svc,
        inbound as inbound_svc,
        portals as portals_svc,
        users as users_svc,
        roles as roles_svc,
        auth_keys as auth_keys_svc,
        plugins as plugins_svc,
        saved_views as saved_views_svc,
        segments as segments_svc,
        scoring as scoring_svc,
    )

    # Admin context — full powers.
    ctx_admin = ServiceContext(user_id=info["admin_id"], role="admin",
                               scope="admin", surface="cli")
    # Non-admin context — `role=user, scope=write` blocks every is_admin()
    # check while still passing can_write/can_read checks.
    ctx_user = ServiceContext(user_id=info["user_id"], role="user",
                              scope="write", surface="cli")
    # Read-only context — blocks every can_write() check.
    ctx_read = ServiceContext(user_id=info["user_id"], role="user",
                              scope="read", surface="cli")

    # ---- [1/13] CONTACTS errors ----
    print("\n[1/13] contacts: VALIDATION + DUP + NOT_FOUND + FORBIDDEN...")
    # Bare contact with no name AND no email is invalid (need at least one).
    _assert_raises("VALIDATION_ERROR",
                   contacts_svc.create, ctx_admin, {})
    # Create one, then try to dup the email.
    c1 = contacts_svc.create(ctx_admin, {
        "full_name": "Anchor", "email": "anchor@err.test",
    })
    _assert_raises("CONTACT_EMAIL_EXISTS",
                   contacts_svc.create, ctx_admin,
                   {"full_name": "Anchor 2", "email": "anchor@err.test"})
    # NOT_FOUND on a guaranteed-missing id.
    _assert_raises("CONTACT_NOT_FOUND",
                   contacts_svc.get, ctx_admin, 999_999)
    # FORBIDDEN: read-scope ctx can't create.
    _assert_raises("FORBIDDEN",
                   contacts_svc.create, ctx_read,
                   {"full_name": "blocked"})
    print(f"  OK   contacts: VALIDATION + CONTACT_EMAIL_EXISTS + "
          f"CONTACT_NOT_FOUND + FORBIDDEN all raised correctly")

    # ---- [2/13] DEALS errors ----
    print("\n[2/13] deals: VALIDATION on missing fields + stage/pipeline mismatch...")
    # Set up a valid pipeline so we can test cross-pipeline misuse.
    p_a = pipelines_svc.create_pipeline(ctx_admin,
                                        {"name": "A", "type": "deal"},
                                        stages=[{"name": "S1"}])
    p_b = pipelines_svc.create_pipeline(ctx_admin,
                                        {"name": "B", "type": "deal"},
                                        stages=[{"name": "S2"}])
    # Missing required title.
    _assert_raises("VALIDATION_ERROR", deals_svc.create, ctx_admin, {
        "pipeline_id": p_a["id"], "stage_id": p_a["stages"][0]["id"],
    })
    # Missing pipeline_id.
    _assert_raises("VALIDATION_ERROR", deals_svc.create, ctx_admin, {
        "title": "no pipeline", "stage_id": p_a["stages"][0]["id"],
    })
    # Stage belongs to a different pipeline.
    _assert_raises("VALIDATION_ERROR", deals_svc.create, ctx_admin, {
        "title": "mismatched stage", "pipeline_id": p_a["id"],
        "stage_id": p_b["stages"][0]["id"],
    })
    # DEAL_NOT_FOUND.
    _assert_raises("DEAL_NOT_FOUND", deals_svc.get, ctx_admin, 999_999)
    print(f"  OK   deals: 3x VALIDATION_ERROR + DEAL_NOT_FOUND raised correctly")

    # ---- [3/13] TASKS errors ----
    print("\n[3/13] tasks: VALIDATION (empty title, bad priority) + NOT_FOUND...")
    _assert_raises("VALIDATION_ERROR", tasks_svc.create, ctx_admin, {"title": ""})
    _assert_raises("VALIDATION_ERROR", tasks_svc.create, ctx_admin,
                   {"title": "ok", "priority": "bogus"})
    _assert_raises("TASK_NOT_FOUND", tasks_svc.get, ctx_admin, 999_999)
    print(f"  OK   tasks: empty-title + bad-priority VALIDATION + TASK_NOT_FOUND raised")

    # ---- [4/13] FORMS errors ----
    print("\n[4/13] forms: bad slug + dup slug + FORM_NOT_FOUND on submit...")
    # Slug must match a strict regex.
    _assert_raises("VALIDATION_ERROR", forms_svc.create, ctx_admin, {
        "slug": "BADCAPS!!", "name": "bad",
        "schema": {"fields": [{"key": "n", "type": "text"}]},
    })
    # Create one, then duplicate its slug.
    forms_svc.create(ctx_admin, {
        "slug": "uno", "name": "Uno",
        "schema": {"fields": [{"key": "n", "type": "text"}]},
    })
    _assert_raises("FORM_SLUG_EXISTS", forms_svc.create, ctx_admin, {
        "slug": "uno", "name": "Uno again",
        "schema": {"fields": [{"key": "n", "type": "text"}]},
    })
    # Submit to non-existent form slug.
    _assert_raises("FORM_NOT_FOUND",
                   forms_svc.submit_public, "no-such-slug", {"name": "x"})
    print(f"  OK   forms: slug-regex VALIDATION + FORM_SLUG_EXISTS + FORM_NOT_FOUND raised")

    # ---- [5/13] INBOUND errors ----
    print("\n[5/13] inbound: bad slug + ENDPOINT_NOT_FOUND + bad signature stored...")
    _assert_raises("VALIDATION_ERROR", inbound_svc.create_endpoint,
                   ctx_admin, slug="BAD!!", name="bad")
    _assert_raises("INBOUND_ENDPOINT_NOT_FOUND",
                   inbound_svc.receive, "no-such-endpoint", b"{}")
    # Create endpoint, then receive with a wrong HMAC. The service
    # short-circuits with status='error' and stores signature_valid=0 in the
    # inbound_events row (event is preserved for forensics, not silently
    # dropped). Verify BOTH the response and the persisted row.
    ep = inbound_svc.create_endpoint(ctx_admin, slug="legit-in", name="legit")
    bad_sig_result = inbound_svc.receive(
        "legit-in", b'{"name":"x"}',
        headers={"X-CRM-Inbound-Signature": "deadbeef" * 8},
    )
    assert bad_sig_result.get("status") == "error" and \
           "signature" in bad_sig_result.get("error", "").lower(), bad_sig_result
    with db() as conn:
        row = conn.execute(
            "SELECT signature_valid, status FROM inbound_events WHERE id=?",
            (bad_sig_result["event_id"],),
        ).fetchone()
    assert row and row["signature_valid"] == 0 and row["status"] == "error", row
    print(f"  OK   inbound: slug VALIDATION + ENDPOINT_NOT_FOUND + bad-sig "
          f"short-circuit + persisted signature_valid=0")

    # ---- [6/13] PORTALS errors ----
    print("\n[6/13] portals: bad scope + bad contact + revoked/expired -> None...")
    _assert_raises("VALIDATION_ERROR",
                   portals_svc.issue, ctx_admin, c1["id"], scope="hacker")
    _assert_raises("CONTACT_NOT_FOUND",
                   portals_svc.issue, ctx_admin, 999_999)
    # Issue a token, revoke it, resolve should return None.
    pt = portals_svc.issue(ctx_admin, c1["id"], scope="client")
    portals_svc.revoke(ctx_admin, pt["id"])
    assert portals_svc.resolve(pt["token"]) is None, \
        "revoked token should resolve to None"
    # Issue then artificially expire via direct DB write — resolve should None.
    pt2 = portals_svc.issue(ctx_admin, c1["id"], scope="client")
    with db() as conn:
        conn.execute(
            "UPDATE portal_tokens SET expires_at=? WHERE id=?",
            (int(time.time()) - 60, pt2["id"]),
        )
    assert portals_svc.resolve(pt2["token"]) is None, \
        "expired token should resolve to None"
    print(f"  OK   portals: scope VALIDATION + CONTACT_NOT_FOUND + "
          f"revoked/expired tokens resolve to None")

    # ---- [7/13] USERS errors ----
    print("\n[7/13] users: bad email/pw/role + dup email + FORBIDDEN...")
    _assert_raises("VALIDATION_ERROR", users_svc.create_user, ctx_admin,
                   email="no-at-sign", password="long-enough", role="user")
    _assert_raises("VALIDATION_ERROR", users_svc.create_user, ctx_admin,
                   email="ok@err.test", password="short", role="user")
    _assert_raises("VALIDATION_ERROR", users_svc.create_user, ctx_admin,
                   email="ok2@err.test", password="long-enough", role="root")
    _assert_raises("USER_EMAIL_EXISTS", users_svc.create_user, ctx_admin,
                   email="admin@err.test", password="long-enough", role="user")
    # Non-admin can't create users.
    _assert_raises("FORBIDDEN", users_svc.create_user, ctx_user,
                   email="rejected@err.test", password="long-enough", role="user")
    print(f"  OK   users: 3x VALIDATION + USER_EMAIL_EXISTS + FORBIDDEN all raised")

    # ---- [8/13] ROLES errors ----
    print("\n[8/13] roles: empty name + ROLE_EXISTS + FORBIDDEN...")
    _assert_raises("VALIDATION_ERROR", roles_svc.create, ctx_admin, "   ")
    roles_svc.create(ctx_admin, "duplicate-role")
    _assert_raises("ROLE_EXISTS", roles_svc.create, ctx_admin, "duplicate-role")
    _assert_raises("FORBIDDEN", roles_svc.create, ctx_user, "blocked-role")
    print(f"  OK   roles: empty-name VALIDATION + ROLE_EXISTS + FORBIDDEN raised")

    # ---- [9/13] AUTH_KEYS errors ----
    print("\n[9/13] auth_keys: VALIDATION + cross-user FORBIDDEN...")
    _assert_raises("VALIDATION_ERROR", auth_keys_svc.create, ctx_admin,
                   info["admin_id"], "key", scope="superuser")
    _assert_raises("VALIDATION_ERROR", auth_keys_svc.create, ctx_admin,
                   info["admin_id"], "", scope="write")
    # Non-admin trying to create a key FOR ANOTHER user.
    _assert_raises("FORBIDDEN", auth_keys_svc.create, ctx_user,
                   info["admin_id"], "stolen-key", scope="admin")
    # Non-admin listing another user's keys.
    _assert_raises("FORBIDDEN", auth_keys_svc.list_for_user, ctx_user,
                   info["admin_id"])
    print(f"  OK   auth_keys: scope+name VALIDATION + cross-user FORBIDDEN raised")

    # ---- [10/13] PLUGINS errors ----
    print("\n[10/13] plugins: PLUGIN_NOT_FOUND + FORBIDDEN on non-admin enable...")
    plugins_svc.reload_all()
    _assert_raises("PLUGIN_NOT_FOUND", plugins_svc.enable, ctx_admin, 999_999)
    _assert_raises("PLUGIN_NOT_FOUND", plugins_svc.disable, ctx_admin, 999_999)
    # Pick any real plug-in id and try as non-admin.
    listed = plugins_svc.list_(ctx_admin)
    if listed:
        any_id = listed[0]["id"]
        _assert_raises("FORBIDDEN", plugins_svc.enable, ctx_user, any_id)
        _assert_raises("FORBIDDEN", plugins_svc.disable, ctx_user, any_id)
        print(f"  OK   plugins: PLUGIN_NOT_FOUND x2 + FORBIDDEN x2 raised")
    else:
        print("  OK   plugins: PLUGIN_NOT_FOUND raised (no plug-ins to test FORBIDDEN)")

    # ---- [11/13] SAVED_VIEWS errors ----
    print("\n[11/13] saved_views: bad entity + empty name...")
    _assert_raises("VALIDATION_ERROR", saved_views_svc.create, ctx_admin,
                   entity="bogus", name="x", config={})
    _assert_raises("VALIDATION_ERROR", saved_views_svc.create, ctx_admin,
                   entity="contact", name="   ", config={})
    _assert_raises("SAVED_VIEW_NOT_FOUND",
                   saved_views_svc.get, ctx_admin, 999_999)
    print(f"  OK   saved_views: entity + name VALIDATION + SAVED_VIEW_NOT_FOUND raised")

    # ---- [12/13] SEGMENTS errors ----
    print("\n[12/13] segments: bad slug + SEGMENT_SLUG_EXISTS + SEGMENT_NOT_FOUND...")
    _assert_raises("VALIDATION_ERROR", segments_svc.create_static, ctx_admin,
                   name="x", slug="BADCAPS", contact_ids=[])
    segments_svc.create_static(ctx_admin, name="seg-a",
                               slug="seg-a", contact_ids=[c1["id"]])
    _assert_raises("SEGMENT_SLUG_EXISTS", segments_svc.create_static, ctx_admin,
                   name="seg-a-again", slug="seg-a", contact_ids=[])
    _assert_raises("SEGMENT_NOT_FOUND",
                   segments_svc.get, ctx_admin, 999_999)
    print(f"  OK   segments: slug VALIDATION + SEGMENT_SLUG_EXISTS + "
          f"SEGMENT_NOT_FOUND raised")

    # ---- [13/13] SCORING errors ----
    # scoring.get_scores on a contact that has no rows yet returns an empty
    # shell rather than raising — but compute on a non-existent contact
    # should fail because of the contacts.get() guard inside.
    print("\n[13/13] scoring: compute on CONTACT_NOT_FOUND...")
    _assert_raises("CONTACT_NOT_FOUND",
                   scoring_svc.compute_for_contact, ctx_admin, 999_999)
    print(f"  OK   scoring: CONTACT_NOT_FOUND raised on bad contact_id")

    print("\nERROR-PATH ACCEPTANCE: PASS")
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
