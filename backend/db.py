"""SQLite connection helper.

Every connection sets the standard PRAGMAs:
  foreign_keys = ON      enforce FKs
  journal_mode = WAL     readers don't block writers (multi-surface friendly)
  busy_timeout = 5000    no random "database is locked" failures

Use `db()` as a context manager; commits on success, rolls back on exception.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get(
    "CRM_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "crm.db"),
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def applied_versions() -> list[int]:
    """Schema versions currently applied to the local DB."""
    with db() as c:
        try:
            return sorted(r[0] for r in c.execute("SELECT version FROM schema_versions"))
        except sqlite3.OperationalError:
            return []


def schema_initialized() -> bool:
    return len(applied_versions()) > 0


def apply_schema(schema_sql: str) -> None:
    """Apply a full SQL script (executescript). Used by setup.py for first-run."""
    with db() as c:
        c.executescript(schema_sql)
