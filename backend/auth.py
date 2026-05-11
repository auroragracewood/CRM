"""Authentication: cookie sessions (humans) + bearer API keys (agents).

Passwords: Argon2id (preferred) or bcrypt (fallback). Never stored raw.
Sessions: server-side rows; 7-day sliding TTL; logout deletes immediately.
API keys: stored hashed (sha256); raw key shown once at creation.
CSRF:    HMAC-SHA256 of session id with CRM_SECRET_KEY.
"""
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

# Password hashing: prefer Argon2, fall back to bcrypt.
_HASH = None
_hasher = None
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    _hasher = PasswordHasher()
    _HASH = "argon2"
except ImportError:
    try:
        import bcrypt  # type: ignore
        _HASH = "bcrypt"
    except ImportError:
        _HASH = None


SESSION_COOKIE_NAME = "crm_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600     # 7 days inactivity
API_KEY_PREFIX = "crm_"                  # raw key looks like "crm_<64hex>"


# ---------- Passwords ----------

def hash_password(plain: str) -> str:
    if _HASH == "argon2":
        return _hasher.hash(plain)
    if _HASH == "bcrypt":
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    raise RuntimeError(
        "No password hashing library installed. pip install argon2-cffi (preferred) or bcrypt."
    )


def verify_password(plain: str, hashed: str) -> bool:
    if _HASH == "argon2":
        try:
            _hasher.verify(hashed, plain)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
    if _HASH == "bcrypt":
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    return False


# ---------- Sessions ----------

def create_session(conn, user_id: int) -> str:
    """Issue a new session id for a user. Returns the cookie value."""
    sid = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute(
        """INSERT INTO sessions (id, user_id, created_at, last_seen_at, expires_at)
           VALUES (?,?,?,?,?)""",
        (sid, user_id, now, now, now + SESSION_TTL_SECONDS),
    )
    return sid


def lookup_session(conn, sid: str) -> Optional[dict]:
    """Look up an active session and slide its expiry. Returns dict or None."""
    if not sid:
        return None
    now = int(time.time())
    row = conn.execute(
        """SELECT s.id, s.user_id, s.expires_at, u.email, u.role, u.display_name
             FROM sessions s
             JOIN users u ON u.id = s.user_id
            WHERE s.id = ? AND s.expires_at > ?""",
        (sid, now),
    ).fetchone()
    if not row:
        return None
    new_exp = now + SESSION_TTL_SECONDS
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
        (now, new_exp, sid),
    )
    return {
        "id": row[0],
        "user_id": row[1],
        "email": row[3],
        "role": row[4],
        "display_name": row[5],
    }


def invalidate_session(conn, sid: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def cleanup_expired_sessions(conn) -> int:
    """Delete sessions past their expiry. Returns count removed."""
    cur = conn.execute(
        "DELETE FROM sessions WHERE expires_at < ?",
        (int(time.time()),),
    )
    return cur.rowcount or 0


# ---------- API keys ----------

def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (raw_key, key_prefix, key_hash).

    Raw key is shown to the user ONCE. Only key_prefix (for UI) and key_hash
    (for verification) are persisted.
    """
    raw = API_KEY_PREFIX + secrets.token_hex(32)
    prefix = raw[:12]                                # "crm_" + 8 hex chars
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, prefix, h


def lookup_api_key(conn, raw_key: str) -> Optional[dict]:
    """Validate a raw API key, update last_used_at on hit."""
    if not raw_key:
        return None
    h = hashlib.sha256(raw_key.encode()).hexdigest()
    row = conn.execute(
        """SELECT id, user_id, scope, revoked_at
             FROM api_keys
            WHERE key_hash = ? AND revoked_at IS NULL""",
        (h,),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
        (int(time.time()), row[0]),
    )
    return {"id": row[0], "user_id": row[1], "scope": row[2]}


def revoke_api_key(conn, key_id: int) -> None:
    conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE id = ?",
        (int(time.time()), key_id),
    )


# ---------- CSRF ----------

def _secret() -> str:
    s = os.environ.get("CRM_SECRET_KEY")
    if not s:
        # In dev, fall back to a deterministic value so reloads don't invalidate all sessions.
        # In production, CRM_ENV=prod + missing CRM_SECRET_KEY should be a startup error.
        s = "dev-insecure-secret-do-not-use-in-prod"
    return s


def csrf_token_for(session_id: str) -> str:
    """Derive a CSRF token from session id + server secret."""
    return hmac.new(_secret().encode(), session_id.encode(), hashlib.sha256).hexdigest()


def verify_csrf(session_id: str, token: str) -> bool:
    if not session_id or not token:
        return False
    return hmac.compare_digest(csrf_token_for(session_id), token)
