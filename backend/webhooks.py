"""Webhook outbox + dispatcher.

Service-layer mutations call `enqueue(conn, event_type, payload)` inside the
same transaction as the data change. After the transaction commits, the
dispatcher (`dispatch_once`) reads pending webhook_events, signs the payload,
delivers it, and updates the row's status / attempts / response. A webhook
failure NEVER rolls back the original CRM mutation.

Signature: HMAC-SHA256 over "{timestamp}.{body}" with the per-webhook secret.
Headers on every delivery:
    X-CRM-Event         the event type (e.g. contact.created)
    X-CRM-Timestamp     unix seconds (the value used in the signature)
    X-CRM-Signature     hex HMAC-SHA256
    X-CRM-Delivery-ID   uuid (idempotency for the receiver)

Private notes are stripped from payloads at the enqueue stage via redact_keys.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional


WEBHOOK_TIMEOUT = int(os.environ.get("CRM_WEBHOOK_TIMEOUT_SECONDS", "5"))
WEBHOOK_MAX_RETRIES = int(os.environ.get("CRM_WEBHOOK_MAX_RETRIES", "5"))


def enqueue(
    conn,
    event_type: str,
    payload: dict,
    *,
    redact_keys: Optional[list[str]] = None,
) -> int:
    """Insert webhook_events rows for every active webhook subscribed to event_type.

    Must run inside the service-layer transaction. Returns the count enqueued.
    """
    now = int(time.time())
    if redact_keys:
        payload = {k: v for k, v in payload.items() if k not in redact_keys}
    payload_json = json.dumps(payload, default=str)

    rows = conn.execute("SELECT id, events_json FROM webhooks WHERE active = 1").fetchall()

    enqueued = 0
    for wh_id, events_json in rows:
        try:
            events = json.loads(events_json or "[]")
        except (TypeError, ValueError):
            continue
        if event_type not in events and "*" not in events:
            continue
        delivery_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO webhook_events
                 (webhook_id, event_type, payload_json, status, attempts,
                  delivery_id, created_at, next_attempt_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (wh_id, event_type, payload_json, "pending", 0, delivery_id, now, now),
        )
        enqueued += 1
    return enqueued


def sign(secret: str, timestamp: int, body: str) -> str:
    msg = f"{timestamp}.{body}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _backoff_seconds(attempts: int) -> int:
    """Exponential backoff capped at 1 hour: 5s, 25s, 125s, 625s, 3125s, then 3600s."""
    return min(5 ** max(1, attempts), 3600)


def dispatch_once(conn, *, limit: int = 50) -> dict:
    """Read pending webhook_events and attempt delivery on each.

    Returns {"attempted": int, "delivered": int, "retrying": int, "failed": int}.
    Safe to call from cron, a foreground loop, or a one-shot CLI command.
    """
    now = int(time.time())
    rows = conn.execute(
        """SELECT we.id, we.event_type, we.payload_json, we.attempts,
                  we.delivery_id, w.url, w.secret
             FROM webhook_events we
             JOIN webhooks w ON w.id = we.webhook_id
            WHERE we.status IN ('pending','retrying')
              AND (we.next_attempt_at IS NULL OR we.next_attempt_at <= ?)
              AND we.attempts < ?
            ORDER BY we.id ASC
            LIMIT ?""",
        (now, WEBHOOK_MAX_RETRIES, limit),
    ).fetchall()

    summary = {"attempted": 0, "delivered": 0, "retrying": 0, "failed": 0}

    for ev_id, evt, body, attempts, delivery_id, url, secret in rows:
        summary["attempted"] += 1
        ts = int(time.time())
        signature = sign(secret, ts, body)
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-CRM-Event": evt,
                "X-CRM-Timestamp": str(ts),
                "X-CRM-Signature": signature,
                "X-CRM-Delivery-ID": delivery_id,
            },
        )
        new_attempts = attempts + 1
        try:
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT) as resp:
                status_code = resp.getcode()
                resp_body = resp.read(2048).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            status_code = e.code
            try:
                resp_body = e.read(2048).decode("utf-8", errors="replace")
            except Exception:
                resp_body = ""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status_code = None
            resp_body = str(e)[:1000]

        if status_code is not None and 200 <= status_code < 300:
            conn.execute(
                """UPDATE webhook_events
                      SET status='delivered', attempts=?, response_status=?,
                          response_body=?, delivered_at=?
                    WHERE id=?""",
                (new_attempts, status_code, resp_body, int(time.time()), ev_id),
            )
            summary["delivered"] += 1
        elif new_attempts >= WEBHOOK_MAX_RETRIES:
            conn.execute(
                """UPDATE webhook_events
                      SET status='failed', attempts=?, response_status=?,
                          response_body=?, failed_at=?
                    WHERE id=?""",
                (new_attempts, status_code, resp_body, int(time.time()), ev_id),
            )
            summary["failed"] += 1
        else:
            next_at = int(time.time()) + _backoff_seconds(new_attempts)
            conn.execute(
                """UPDATE webhook_events
                      SET status='retrying', attempts=?, response_status=?,
                          response_body=?, next_attempt_at=?
                    WHERE id=?""",
                (new_attempts, status_code, resp_body, next_at, ev_id),
            )
            summary["retrying"] += 1
    return summary
