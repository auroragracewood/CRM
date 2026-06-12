"""Webhook delivery integration test.

Existing tests verify webhook_events rows get ENQUEUED. None verify the
delivery half:
  - dispatcher picks up pending rows
  - signs payload with HMAC-SHA256(secret, "{ts}.{body}")
  - includes the documented headers (Event / Timestamp / Signature / Delivery-ID)
  - on 2xx response, marks row 'delivered'
  - on 5xx response, marks 'retrying' with exponential backoff next_attempt_at
  - after WEBHOOK_MAX_RETRIES attempts, marks 'failed'
  - event-type filter: a webhook subscribed only to "deal.created" doesn't
    receive rows for "contact.created"

Every one of these has zero test coverage today. If the dispatcher silently
breaks any of these contracts, an integration partner gets bad/no events.

This test runs a tiny in-process HTTP receiver on a free port, subscribes a
real webhook to it, triggers real CRM mutations, and inspects what arrived.

Usage:
  python -m tests.test_webhook_delivery
"""
import hashlib
import hmac
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# ---------- in-process HTTP receiver ----------

class _Receiver:
    """Tiny HTTP server that captures POSTs and replies with a configurable
    status. Runs in a thread, exposes the captured requests via .events."""

    def __init__(self):
        self.events: list[dict] = []
        self.response_status = 200      # mutable per test block
        self.response_body = '{"ok": true}'
        self._server = None
        self._thread = None
        self.port = None

    def start(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.events.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                self.send_response(outer.response_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.response_body)))
                self.end_headers()
                self.wfile.write(outer.response_body.encode())

            def log_message(self, *a, **kw):
                pass  # suppress noisy stderr

        # Bind to an ephemeral port.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}/hook"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()


# ---------- harness ----------

def _setup_temp_db():
    tmpdir = tempfile.mkdtemp(prefix="crm_test_wh_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"   # we drive dispatch manually
    os.environ["CRM_SECRET_KEY"] = "test-secret-wh"
    # Force a low retry cap so block [3] can prove the 'failed' transition
    # without 5+ dispatch loops.
    os.environ["CRM_WEBHOOK_MAX_RETRIES"] = "2"
    # Short delivery timeout: we don't expect any slow paths in-process.
    os.environ["CRM_WEBHOOK_TIMEOUT_SECONDS"] = "3"

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
            ("admin@wh.test", pw_hash, "WH Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"tmpdir": tmpdir, "db_path": db_path, "user_id": user_id}


def _subscribe_webhook(conn, url, events, secret) -> int:
    now = int(time.time())
    conn.execute(
        "INSERT INTO webhooks (url, events_json, secret, active, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (url, json.dumps(events), secret, 1, now, now),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------- the test ----------

def run():
    print("Setting up temp DB + in-process HTTP receiver...")
    info = _setup_temp_db()

    receiver = _Receiver()
    url = receiver.start()
    print(f"  DB:       {info['db_path']}")
    print(f"  Receiver: {url}")

    from backend.context import ServiceContext
    from backend.db import db
    from backend import webhooks
    from backend.services import contacts as contacts_svc, pipelines as pipelines_svc, deals as deals_svc

    ctx = ServiceContext(user_id=info["user_id"], role="admin",
                         scope="admin", surface="cli")
    SECRET = "very-secret-shhh"

    try:
        # ---- [1/4] HAPPY PATH: enqueue, dispatch, verify delivery + HMAC ----
        print("\n[1/4] enqueue -> dispatch -> verify delivery + HMAC...")
        with db() as conn:
            wh_id = _subscribe_webhook(conn, url,
                                       ["contact.created"], SECRET)
        # Create a contact — service-layer enqueues a webhook_events row.
        c = contacts_svc.create(ctx, {"full_name": "WH One",
                                      "email": "wh1@example.test"})
        # Dispatch.
        with db() as conn:
            summary = webhooks.dispatch_once(conn)
        assert summary["delivered"] == 1, f"expected 1 delivered, got {summary}"

        # Verify the receiver got it.
        assert len(receiver.events) == 1, receiver.events
        ev = receiver.events[0]
        # Headers (BaseHTTPRequestHandler lowercases when we asked).
        h = ev["headers"]
        assert h.get("x-crm-event") == "contact.created", h
        delivery_id = h.get("x-crm-delivery-id")
        assert delivery_id and len(delivery_id) == 36, h  # uuid format
        ts = int(h["x-crm-timestamp"])
        sig = h["x-crm-signature"]

        # Verify HMAC matches what webhooks.sign would compute.
        body_text = ev["body"].decode()
        expected_sig = webhooks.sign(SECRET, ts, body_text)
        assert hmac.compare_digest(sig, expected_sig), \
            f"HMAC mismatch: got {sig}, expected {expected_sig}"

        # Verify the payload references the contact.
        payload = json.loads(body_text)
        assert payload.get("contact", {}).get("id") == c["id"], payload

        # Verify the DB row is now status='delivered' with response_status=200.
        with db() as conn:
            row = conn.execute(
                "SELECT status, attempts, response_status FROM webhook_events "
                "WHERE webhook_id=?", (wh_id,),
            ).fetchone()
        assert row["status"] == "delivered", row
        assert row["attempts"] == 1, row
        assert row["response_status"] == 200, row
        print(f"  OK   delivered to {url} with valid HMAC; DB row="
              f"{dict(row)}")

        # ---- [2/4] RETRY: 5xx response -> status='retrying' + backoff ----
        print("\n[2/4] failing response -> status='retrying' with backoff...")
        receiver.events.clear()
        receiver.response_status = 500
        receiver.response_body = '{"err": "server down"}'

        contacts_svc.create(ctx, {"full_name": "WH Two",
                                  "email": "wh2@example.test"})
        with db() as conn:
            summary = webhooks.dispatch_once(conn)
        assert summary["retrying"] == 1, f"expected retrying, got {summary}"

        with db() as conn:
            # The 'retrying' row is the most recent one — order by id desc.
            row = conn.execute(
                "SELECT id, status, attempts, response_status, next_attempt_at "
                "FROM webhook_events WHERE webhook_id=? ORDER BY id DESC LIMIT 1",
                (wh_id,),
            ).fetchone()
        assert row["status"] == "retrying", row
        assert row["attempts"] == 1, row
        assert row["response_status"] == 500, row
        # Backoff: next_attempt_at should be in the future (5^1 = 5s minimum).
        assert row["next_attempt_at"] > int(time.time()), \
            f"next_attempt_at must be in the future for retry: {dict(row)}"
        retry_event_id = row["id"]
        print(f"  OK   row id={retry_event_id} status=retrying attempts=1 "
              f"next_attempt_at=+{row['next_attempt_at'] - int(time.time())}s")

        # ---- [3/4] EXHAUST RETRIES: force next_attempt_at=now, dispatch -> 'failed' ----
        # MAX_RETRIES=2 (set in env), so the second attempt that still fails
        # should flip the row to status='failed'.
        print("\n[3/4] forcing retry past MAX_RETRIES -> status='failed'...")
        with db() as conn:
            conn.execute(
                "UPDATE webhook_events SET next_attempt_at=? WHERE id=?",
                (int(time.time()) - 1, retry_event_id),
            )
        with db() as conn:
            summary = webhooks.dispatch_once(conn)
        assert summary["failed"] == 1, f"expected failed, got {summary}"
        with db() as conn:
            row = conn.execute(
                "SELECT status, attempts FROM webhook_events WHERE id=?",
                (retry_event_id,),
            ).fetchone()
        assert row["status"] == "failed", row
        assert row["attempts"] == 2, row
        print(f"  OK   row id={retry_event_id} flipped to status=failed "
              f"after {row['attempts']} attempts (max={os.environ['CRM_WEBHOOK_MAX_RETRIES']})")

        # ---- [4/4] EVENT-TYPE FILTER: deal-only sub gets no contact events ----
        # Add a second subscription that ONLY listens for deal.created. Then
        # trigger a contact event and verify no row gets enqueued for it.
        print("\n[4/4] event-type filter: deal-only sub ignores contact events...")
        receiver.events.clear()
        receiver.response_status = 200
        with db() as conn:
            deal_only_id = _subscribe_webhook(conn, url,
                                              ["deal.created"], SECRET)

        # Snapshot baseline event count for the deal-only webhook.
        with db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM webhook_events WHERE webhook_id=?",
                (deal_only_id,),
            ).fetchone()[0]

        # Trigger a contact event — deal-only sub MUST NOT get a row.
        contacts_svc.create(ctx, {"full_name": "WH Three",
                                  "email": "wh3@example.test"})
        with db() as conn:
            after_contact = conn.execute(
                "SELECT COUNT(*) FROM webhook_events WHERE webhook_id=?",
                (deal_only_id,),
            ).fetchone()[0]
        assert after_contact == before, \
            f"deal-only sub erroneously got a contact event row: {after_contact} vs {before}"

        # Now trigger a deal event — deal-only sub SHOULD get a row.
        pipeline = pipelines_svc.create_pipeline(
            ctx, {"name": "WH P", "type": "deal"},
            stages=[{"name": "S"}],
        )
        deals_svc.create(ctx, {
            "title": "WH deal", "pipeline_id": pipeline["id"],
            "stage_id": pipeline["stages"][0]["id"],
        })
        with db() as conn:
            after_deal = conn.execute(
                "SELECT COUNT(*) FROM webhook_events WHERE webhook_id=?",
                (deal_only_id,),
            ).fetchone()[0]
        assert after_deal == before + 1, \
            f"deal-only sub didn't pick up deal event: {after_deal} vs {before}+1"
        print(f"  OK   deal-only sub: contact event ignored ({before} rows), "
              f"deal event enqueued ({after_deal} rows)")

    finally:
        receiver.stop()

    print("\nWEBHOOK DELIVERY ACCEPTANCE: PASS")
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
