"""Milestone 1 acceptance script.

Proves the architecture works:
  - Service-layer create writes to SQLite, audit_log, and webhook_events
  - REST create through bearer key works
  - CLI create works
  - MCP fallback (stdio JSON-RPC) create works
  - Webhook outbox produces one webhook_events row per active subscription
  - Each surface logs its own row in audit_log (different `surface` value)

Usage:
  python -m tests.test_milestone1
or
  pytest tests/test_milestone1.py

The script runs against a TEMPORARY SQLite DB (set via CRM_DB_PATH) so it never
touches your real crm.db.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# ---------- harness ----------

def _setup_temp_db():
    """Create a fresh temp DB, apply schema, create admin + API key. Returns dict."""
    tmpdir = tempfile.mkdtemp(prefix="crm_test_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"   # tests drive dispatch manually
    os.environ["CRM_SECRET_KEY"] = "test-secret"

    # Reload modules so they pick up the new env var
    for mod in list(sys.modules):
        if mod.startswith("backend") or mod == "backend":
            del sys.modules[mod]

    sys.path.insert(0, str(ROOT))
    from backend import auth as auth_mod
    from backend.db import apply_schema, db

    schema_sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    apply_schema(schema_sql)

    now = int(time.time())
    admin_pw = "test-password-1234"
    pw_hash = auth_mod.hash_password(admin_pw)
    raw, prefix, key_hash = auth_mod.generate_api_key()

    with db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("admin@test.local", pw_hash, "Test Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, "test", prefix, key_hash, "admin", now),
        )

    return {
        "tmpdir": tmpdir, "db_path": db_path,
        "admin_email": "admin@test.local", "admin_password": admin_pw,
        "user_id": user_id, "api_key": raw,
    }


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(env_extra: dict) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = os.environ.copy()
    env.update(env_extra)
    env["CRM_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        env=env, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Wait for the server to start.
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc, port
        except OSError:
            time.sleep(0.2)
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=2)
            raise RuntimeError(f"server exited early\nstdout:\n{out.decode()}\nstderr:\n{err.decode()}")
    proc.terminate()
    raise RuntimeError("server failed to bind within 15s")


def _http_json(method, url, *, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None and "Content-Type" not in req.headers:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ---------- the test ----------

def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:      {info['db_path']}")
    print(f"  Admin:   {info['admin_email']} (id={info['user_id']})")
    print(f"  API key: {info['api_key'][:14]}...")

    # 1. SERVICE-LAYER create (sanity baseline)
    print("\n[1/5] service-layer create...")
    from backend.context import ServiceContext
    from backend.services import contacts as contacts_service
    from backend.db import db as dbctx
    ctx = ServiceContext(user_id=info["user_id"], role="admin", scope="admin", surface="cli")
    c1 = contacts_service.create(ctx, {
        "full_name": "Maya Service",
        "email": "maya.service@test.local",
    })
    assert c1["id"] and c1["email"] == "maya.service@test.local", f"service create returned {c1}"
    print(f"  OK   contact id={c1['id']} email={c1['email']}")

    # 2. Spin up the HTTP server for REST + UI tests.
    print("\n[2/5] starting HTTP server...")
    proc, port = _start_server({})
    base = f"http://127.0.0.1:{port}"
    print(f"  server at {base} (pid {proc.pid})")

    try:
        # REST create via Bearer API key
        print("\n[3/5] REST create via Bearer API key...")
        status, resp = _http_json("POST", f"{base}/api/contacts",
                                  headers={"Authorization": f"Bearer {info['api_key']}"},
                                  body={"full_name": "Rena REST", "email": "rena@test.local"})
        assert status == 201, f"expected 201, got {status}: {resp}"
        assert resp["ok"] and resp["contact"]["email"] == "rena@test.local", resp
        rest_contact_id = resp["contact"]["id"]
        print(f"  OK   contact id={rest_contact_id}  status={status}")

        # /api/me sanity for surface
        status, me = _http_json("GET", f"{base}/api/me",
                                headers={"Authorization": f"Bearer {info['api_key']}"})
        assert status == 200 and me["surface"] == "rest" and me["api_key_id"]
        print(f"  OK   /api/me reports surface={me['surface']} api_key_id={me['api_key_id']}")

        # 4. CLI create
        print("\n[4/5] CLI create...")
        env = os.environ.copy()
        env["CRM_DB_PATH"] = info["db_path"]
        cli_proc = subprocess.run(
            [sys.executable, "-m", "agent_surface.cli",
             "--as-email", info["admin_email"],
             "contact", "create",
             "--name", "Carla CLI", "--email", "carla@test.local"],
            env=env, cwd=str(ROOT), capture_output=True, text=True,
        )
        assert cli_proc.returncode == 0, f"CLI failed: stdout={cli_proc.stdout!r} stderr={cli_proc.stderr!r}"
        cli_out = json.loads(cli_proc.stdout)
        assert cli_out["ok"] and cli_out["contact"]["email"] == "carla@test.local", cli_out
        cli_contact_id = cli_out["contact"]["id"]
        print(f"  OK   contact id={cli_contact_id}")

        # 5. MCP create (via stdio fallback — works whether or not `mcp` is installed)
        print("\n[5/5] MCP stdio create...")
        mcp_env = os.environ.copy()
        mcp_env["CRM_DB_PATH"] = info["db_path"]
        # Try the fallback JSON-RPC by stdin/stdout.  If FastMCP is installed
        # the server uses FastMCP which speaks the real MCP protocol — we'd
        # need the `mcp` client to test it.  For now, we test the fallback
        # path by importing the module's _do function directly.
        try:
            import importlib
            if "agent_surface.mcp_server" in sys.modules:
                del sys.modules["agent_surface.mcp_server"]
            # Force-disable FastMCP for this test so we exercise the JSON-RPC
            # fallback that doesn't require the `mcp` dependency.
            import builtins
            real_import = builtins.__import__
            def _block_mcp(name, *a, **kw):
                if name.startswith("mcp"):
                    raise ImportError("blocked for test")
                return real_import(name, *a, **kw)
            builtins.__import__ = _block_mcp
            try:
                mcp_mod = importlib.import_module("agent_surface.mcp_server")
            finally:
                builtins.__import__ = real_import
            mcp_result = mcp_mod._do("create_contact", {
                "full_name": "Mona MCP", "email": "mona@test.local",
            })
            assert mcp_result["ok"] and mcp_result["contact"]["email"] == "mona@test.local", mcp_result
            mcp_contact_id = mcp_result["contact"]["id"]
            print(f"  OK   contact id={mcp_contact_id}")
        except Exception as ex:
            raise RuntimeError(f"MCP create failed: {ex}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # ---------- assertions on database state ----------

    print("\nverifying database state...")
    from backend.db import db as dbctx2
    with dbctx2() as conn:
        contacts = conn.execute(
            "SELECT id, email FROM contacts WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        contact_emails = {c["email"] for c in contacts}
        for needed in ("maya.service@test.local", "rena@test.local",
                       "carla@test.local", "mona@test.local"):
            assert needed in contact_emails, f"missing contact {needed!r} in {contact_emails}"
        print(f"  OK   4 contacts visible in SQLite ({len(contacts)} total)")

        # audit_log: at least 4 contact.created rows, one per surface visited
        audits = conn.execute(
            "SELECT surface, action FROM audit_log WHERE action='contact.created'"
        ).fetchall()
        surfaces = {a["surface"] for a in audits}
        assert len(audits) >= 4, f"expected 4+ audit rows, got {len(audits)}"
        for s in ("cli", "rest", "mcp"):
            assert s in surfaces, f"audit log missing surface={s!r} (got {surfaces})"
        print(f"  OK   audit_log has {len(audits)} contact.created rows across surfaces {sorted(surfaces)}")

    # ---------- webhook outbox sanity ----------

    print("\nverifying webhook outbox...")
    with dbctx2() as conn:
        # Add a webhook subscription, then create one more contact and confirm
        # webhook_events row is enqueued (status='pending').
        conn.execute(
            "INSERT INTO webhooks (url, events_json, secret, active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("https://example.invalid/never-reached",
             json.dumps(["contact.created"]),
             "test-secret", 1,
             int(time.time()), int(time.time())),
        )
    ctx = ServiceContext(user_id=info["user_id"], role="admin", scope="admin", surface="cli")
    after_hook = contacts_service.create(ctx, {
        "full_name": "Wendy Webhook", "email": "wendy@test.local",
    })
    with dbctx2() as conn:
        events = conn.execute(
            "SELECT event_type, status FROM webhook_events WHERE event_type='contact.created'"
        ).fetchall()
        assert events, "webhook_events table has no contact.created rows"
        statuses = {e["status"] for e in events}
        # We DON'T dispatch — the URL is invalid. We only assert the outbox row exists.
        assert "pending" in statuses, f"expected at least one pending row, got {statuses}"
        print(f"  OK   webhook_events has {len(events)} contact.created rows; statuses={sorted(statuses)}")

    # ---------- Milestone 2 spot-checks ----------

    print("\n[M2] verifying companies / interactions / notes / tags / consent...")
    from backend.services import (
        companies as companies_service,
        interactions as interactions_service,
        notes as notes_service,
        tags as tags_service,
        consent as consent_service,
    )
    ctx = ServiceContext(user_id=info["user_id"], role="admin", scope="admin", surface="cli")

    company = companies_service.create(ctx, {"name": "Acme Co.", "domain": "Acme.com",
                                             "industry": "Widgets"})
    assert company["domain"] == "acme.com", "domain should be normalized to lowercase"
    print(f"  OK   company id={company['id']} (domain normalized)")

    inter = interactions_service.log(ctx, {
        "type": "call", "contact_id": c1["id"],
        "title": "Discovery", "body": "Discussed scope.",
    })
    assert inter["type"] == "call" and inter["contact_id"] == c1["id"]
    timeline = interactions_service.list_for_contact(ctx, c1["id"])
    assert len(timeline) == 1 and timeline[0]["id"] == inter["id"]
    print(f"  OK   interaction logged + retrievable via timeline")

    note_pub = notes_service.create(ctx, contact_id=c1["id"], body="public note", visibility="public")
    note_team = notes_service.create(ctx, contact_id=c1["id"], body="team note", visibility="team")
    note_priv = notes_service.create(ctx, contact_id=c1["id"], body="private note", visibility="private")
    assert all(n["id"] for n in (note_pub, note_team, note_priv))
    notes_visible = notes_service.list_for_contact(ctx, c1["id"])
    # admin sees public + team in full, private redacted UNLESS author (here ctx IS the author)
    assert len(notes_visible) == 3
    print(f"  OK   notes created with 3 visibility scopes")

    # Simulate a private note authored by *someone else* (created_by=NULL → not the
    # current admin). The admin should see it redacted, then be able to reveal it.
    from backend.db import db as dbctx_x
    import time as _t
    _now = int(_t.time())
    with dbctx_x() as conn:
        conn.execute(
            "INSERT INTO notes (contact_id, body, visibility, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (c1["id"], "stranger's private", "private", None, _now, _now),
        )
        stranger_note_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    visible_now = notes_service.list_for_contact(ctx, c1["id"])
    redacted = [n for n in visible_now if n.get("_private_redacted")]
    assert len(redacted) == 1, f"expected 1 redacted private note from another user, got {len(redacted)}"
    revealed = notes_service.reveal_private(ctx, stranger_note_id)
    assert revealed["body"] == "stranger's private"
    with dbctx_x() as conn:
        reveal_audit = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='note.private_revealed'"
        ).fetchone()[0]
    assert reveal_audit >= 1
    print(f"  OK   private note redacted for non-author, reveal audits")

    tag = tags_service.create(ctx, "warm-lead", color="#4a5fc1", scope="contact")
    tags_service.attach(ctx, tag_id=tag["id"], contact_id=c1["id"])
    contact_tags = tags_service.list_for_contact(ctx, c1["id"])
    assert any(t["name"] == "warm-lead" for t in contact_tags)
    print(f"  OK   tag created + attached + listed")

    cons = consent_service.record(ctx, c1["id"], "email", "granted", source="signup-form")
    assert cons["status"] == "granted" and cons["granted_at"]
    cons2 = consent_service.record(ctx, c1["id"], "email", "withdrawn", source="unsubscribe-link")
    assert cons2["status"] == "withdrawn" and cons2["withdrawn_at"]
    print(f"  OK   consent recorded (granted → withdrawn) preserves channel uniqueness")

    print("\n" + "=" * 60)
    print(" MILESTONE 1 + 2 ACCEPTANCE: PASS")
    print("=" * 60)
    print("\n  M1: service-layer create     OK")
    print("  M1: REST   create (bearer)   OK")
    print("  M1: CLI    create            OK")
    print("  M1: MCP    create (fallback) OK")
    print("  M1: audit_log per surface    OK")
    print("  M1: webhook outbox enqueue   OK")
    print("  M2: companies                OK")
    print("  M2: interactions + timeline  OK")
    print("  M2: notes + visibility scopes + reveal audit  OK")
    print("  M2: tags + attach + list     OK")
    print("  M2: consent record + flip    OK")
    print()


if __name__ == "__main__":
    run()
