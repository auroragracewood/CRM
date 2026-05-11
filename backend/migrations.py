"""Migration runner. Applies migrations/*.sql files in lexical order.

Each file is expected to end with an INSERT into schema_versions guarded by
`WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = N)` so re-runs
are safe even when individual statements use CREATE TABLE IF NOT EXISTS.
"""
import re
from pathlib import Path

from .db import db


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_VERSION_RE = re.compile(r"^(\d+)_")


def list_pending(applied: set[int]) -> list[tuple[int, Path]]:
    """Return [(version, path)] for migrations not yet applied, in version order."""
    out = []
    if not MIGRATIONS_DIR.exists():
        return out
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _VERSION_RE.match(p.name)
        if not m:
            continue
        v = int(m.group(1))
        if v not in applied:
            out.append((v, p))
    return out


def applied_versions() -> set[int]:
    with db() as c:
        try:
            return {r[0] for r in c.execute("SELECT version FROM schema_versions")}
        except Exception:
            return set()


def run_pending(verbose: bool = True) -> list[int]:
    applied = applied_versions()
    pending = list_pending(applied)
    ran = []
    for v, p in pending:
        sql = p.read_text(encoding="utf-8")
        if verbose:
            print(f"  applying migration {v}: {p.name} ...")
        with db() as conn:
            conn.executescript(sql)
        ran.append(v)
    return ran
