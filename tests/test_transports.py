"""Transport coverage acceptance script.

test_milestone1 proves the REST+CLI+MCP pattern works for contacts. This
file proves the pattern holds for the v1 resources too — deals + tasks —
across all three transports.

Each transport gets exercised once per resource. The point isn't to
re-test the service-layer behaviour (other test files cover that) — it's
to prove the *transport wiring* is intact: that REST handlers correctly
parse JSON payloads, that CLI argparse flags map to service args, that
MCP tool names resolve to the right service function.

If any transport silently breaks for a new resource — say someone renames
a CLI flag or removes an MCP tool — this test catches it.

Usage:
  python -m tests.test_transports
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# ---------- harness (matches test_milestone1 conventions) ----------

def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_xport_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-xport"

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
    admin_pw = "test-password-1234"
    pw_hash = auth_mod.hash_password(admin_pw)
    raw, prefix, key_hash = auth_mod.generate_api_key()

    with db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("admin@xport.test", pw_hash, "Xport Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, "test", prefix, key_hash, "admin", now),
        )

    return {
        "tmpdir": tmpdir, "db_path": db_path,
        "admin_email": "admin@xport.test", "admin_password": admin_pw,
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


def _load_mcp_fallback():
    """Force-load mcp_server with FastMCP blocked so we exercise the stdio
    JSON-RPC fallback path that doesn't require the `mcp` dependency."""
    import builtins
    import importlib
    if "agent_surface.mcp_server" in sys.modules:
        del sys.modules["agent_surface.mcp_server"]
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
    return mcp_mod


# ---------- the test ----------

def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:      {info['db_path']}")
    print(f"  Admin:   {info['admin_email']} (id={info['user_id']})")
    print(f"  API key: {info['api_key'][:14]}...")

    # Bootstrap one pipeline + stage at the service layer so deal transport
    # tests have valid pipeline_id/stage_id to use. This is shared by REST,
    # CLI, and MCP blocks below.
    from backend.context import ServiceContext
    from backend.services import pipelines as pipelines_svc
    ctx = ServiceContext(user_id=info["user_id"], role="admin",
                         scope="admin", surface="cli")
    pipeline = pipelines_svc.create_pipeline(
        ctx, {"name": "Xport pipeline", "type": "deal"},
        stages=[{"name": "Discovery", "position": 0}],
    )
    pipeline_id = pipeline["id"]
    stage_id = pipeline["stages"][0]["id"]
    print(f"  Pipeline: id={pipeline_id} stage={stage_id}")

    # Start the HTTP server once — used by both REST blocks.
    print("\nstarting HTTP server...")
    proc, port = _start_server({})
    base = f"http://127.0.0.1:{port}"
    auth_headers = {"Authorization": f"Bearer {info['api_key']}"}
    print(f"  server at {base} (pid {proc.pid})")

    try:
        # ---- [1/6] REST: POST /api/deals ----
        print("\n[1/6] REST POST /api/deals...")
        status, resp = _http_json("POST", f"{base}/api/deals",
                                  headers=auth_headers,
                                  body={"title": "REST deal",
                                        "pipeline_id": pipeline_id,
                                        "stage_id": stage_id,
                                        "value": 1000, "currency": "USD"})
        assert status == 201 and resp["ok"], f"{status}: {resp}"
        assert resp["deal"]["title"] == "REST deal", resp
        rest_deal_id = resp["deal"]["id"]
        print(f"  OK   deal id={rest_deal_id} status={status}")

        # ---- [2/6] CLI: deal create ----
        print("\n[2/6] CLI deal create...")
        env = os.environ.copy()
        env["CRM_DB_PATH"] = info["db_path"]
        cli_proc = subprocess.run(
            [sys.executable, "-m", "agent_surface.cli",
             "--as-email", info["admin_email"],
             "deal", "create",
             "--title", "CLI deal",
             "--pipeline-id", str(pipeline_id),
             "--stage-id", str(stage_id),
             "--currency", "USD"],
            env=env, cwd=str(ROOT), capture_output=True, text=True,
        )
        assert cli_proc.returncode == 0, \
            f"CLI failed: stdout={cli_proc.stdout!r} stderr={cli_proc.stderr!r}"
        cli_out = json.loads(cli_proc.stdout)
        assert cli_out["ok"] and cli_out["deal"]["title"] == "CLI deal", cli_out
        cli_deal_id = cli_out["deal"]["id"]
        print(f"  OK   deal id={cli_deal_id}")

        # ---- [3/6] MCP: create_deal via stdio fallback ----
        print("\n[3/6] MCP create_deal (stdio fallback)...")
        mcp_mod = _load_mcp_fallback()
        mcp_result = mcp_mod._do("create_deal", {
            "title": "MCP deal",
            "pipeline_id": pipeline_id,
            "stage_id": stage_id,
        })
        assert mcp_result["ok"] and mcp_result["deal"]["title"] == "MCP deal", mcp_result
        mcp_deal_id = mcp_result["deal"]["id"]
        print(f"  OK   deal id={mcp_deal_id}")

        # ---- [4/6] REST: POST /api/tasks ----
        print("\n[4/6] REST POST /api/tasks...")
        status, resp = _http_json("POST", f"{base}/api/tasks",
                                  headers=auth_headers,
                                  body={"title": "REST task",
                                        "priority": "high"})
        assert status == 201 and resp["ok"], f"{status}: {resp}"
        assert resp["task"]["title"] == "REST task", resp
        rest_task_id = resp["task"]["id"]
        print(f"  OK   task id={rest_task_id} status={status}")

        # ---- [5/6] CLI: task create ----
        print("\n[5/6] CLI task create...")
        cli_proc = subprocess.run(
            [sys.executable, "-m", "agent_surface.cli",
             "--as-email", info["admin_email"],
             "task", "create",
             "--title", "CLI task",
             "--priority", "normal"],
            env=env, cwd=str(ROOT), capture_output=True, text=True,
        )
        assert cli_proc.returncode == 0, \
            f"CLI failed: stdout={cli_proc.stdout!r} stderr={cli_proc.stderr!r}"
        cli_out = json.loads(cli_proc.stdout)
        assert cli_out["ok"] and cli_out["task"]["title"] == "CLI task", cli_out
        cli_task_id = cli_out["task"]["id"]
        print(f"  OK   task id={cli_task_id}")

        # ---- [6/6] MCP: create_task via stdio fallback ----
        print("\n[6/6] MCP create_task (stdio fallback)...")
        mcp_result = mcp_mod._do("create_task", {"title": "MCP task"})
        assert mcp_result["ok"] and mcp_result["task"]["title"] == "MCP task", mcp_result
        mcp_task_id = mcp_result["task"]["id"]
        print(f"  OK   task id={mcp_task_id}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # ---- AUDIT cross-surface verification ----
    # Each resource should have audit rows from exactly three surfaces.
    # If any surface silently swallows audit writes (regression), this fails.
    print("\n[audit] cross-surface audit_log check on deals + tasks...")
    from backend.db import db
    with db() as conn:
        deal_audits = conn.execute(
            "SELECT surface FROM audit_log WHERE action='deal.created'"
        ).fetchall()
        deal_surfaces = {a["surface"] for a in deal_audits}
        task_audits = conn.execute(
            "SELECT surface FROM audit_log WHERE action='task.created'"
        ).fetchall()
        task_surfaces = {a["surface"] for a in task_audits}

    for needed in ("rest", "cli", "mcp"):
        assert needed in deal_surfaces, \
            f"deal.created audit missing surface={needed!r} (got {deal_surfaces})"
        assert needed in task_surfaces, \
            f"task.created audit missing surface={needed!r} (got {task_surfaces})"
    print(f"  OK   deal.created surfaces: {sorted(deal_surfaces)}")
    print(f"  OK   task.created surfaces: {sorted(task_surfaces)}")

    print("\nTRANSPORT ACCEPTANCE: PASS")
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
