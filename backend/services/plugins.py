"""Plug-in framework.

A plug-in is a Python module placed under `agent_surface/plugins/`. The CRM
discovers and loads plug-ins at startup (and on-demand via reload()). Each
plug-in module declares:

    NAME = "fit-score-icp"          # required, unique
    VERSION = "0.1.0"               # optional
    DESCRIPTION = "..."             # optional

And one or more hook functions:

    def on_contact_created(ctx, contact: dict, conn) -> None:
        ...
    def on_deal_stage_changed(ctx, deal: dict, from_stage: int, to_stage: int, conn) -> None:
        ...
    def compute_fit_score(ctx, contact_id: int) -> tuple[int, list[dict]] | None:
        ...     # returns (score, evidence_list) or None to fall back to default

Hooks are discovered by NAME — any callable in the module whose name matches
a registered hook name participates. Plug-ins can also register custom MCP
tools by exposing a `MCP_TOOLS = {name: fn}` mapping (caller wires it up).

Registry: every plug-in has a row in the `plugins` table; enable/disable
is reversible without uninstalling. Errors are caught + logged to
`plugins.last_error` so a broken plug-in doesn't crash the server.
"""
import importlib
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
# NB: don't import ServiceError at module-load time — plugins.py is imported
# by contacts.py for hook dispatch, so a top-level reverse import would create
# a circular load. ServiceError is imported lazily inside the functions that
# need it.


# Known hook names. New hooks can be added freely; plug-ins that don't define
# a function with that name are simply skipped.
KNOWN_HOOKS = (
    "on_contact_created",
    "on_contact_updated",
    "on_contact_deleted",
    "on_company_created",
    "on_company_updated",
    "on_interaction_logged",
    "on_note_created",
    "on_deal_created",
    "on_deal_updated",
    "on_deal_stage_changed",
    "on_task_created",
    "on_task_completed",
    "on_form_submitted",
    "on_inbound_received",
    "on_scoring_computed",
    "compute_fit_score",       # special: returns (score, evidence) instead of running for side-effect
)


PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "agent_surface" / "plugins"


# ---------- in-process registry ----------

_LOADED: dict[str, dict] = {}      # name -> {module, meta, hooks: {hook_name: callable}}


def _hooks_for(module) -> dict[str, Callable]:
    """Discover callables on the module matching KNOWN_HOOKS names."""
    out = {}
    for h in KNOWN_HOOKS:
        fn = getattr(module, h, None)
        if callable(fn):
            out[h] = fn
    return out


def _module_meta(module) -> dict:
    return {
        "name": getattr(module, "NAME", None),
        "version": getattr(module, "VERSION", None),
        "description": getattr(module, "DESCRIPTION", None),
    }


def _load_one(path: Path) -> Optional[dict]:
    """Load (or reload) a single plug-in file. Returns the registry entry or None."""
    spec = importlib.util.spec_from_file_location(f"_crm_plugin_{path.stem}", path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return {"error": traceback.format_exc(), "path": str(path)}
    meta = _module_meta(module)
    if not meta.get("name"):
        return {"error": f"plug-in {path.name} missing required NAME constant",
                "path": str(path)}
    return {
        "module": module,
        "meta": meta,
        "hooks": _hooks_for(module),
        "path": str(path),
    }


def discover() -> list[Path]:
    """Return all .py files in PLUGIN_DIR (excluding READMEs / __init__)."""
    if not PLUGIN_DIR.exists():
        return []
    return sorted(
        p for p in PLUGIN_DIR.glob("*.py")
        if not p.name.startswith("_") and p.stem != "__init__"
    )


def reload_all() -> dict:
    """Discover and load every plug-in. Registers each in the DB if missing.
    Returns a summary of loaded/errors."""
    _LOADED.clear()
    summary = {"loaded": [], "errors": []}
    now = int(time.time())
    paths = discover()
    with db() as conn:
        for p in paths:
            entry = _load_one(p)
            if entry is None:
                summary["errors"].append({"path": str(p), "error": "could not spec-load"})
                continue
            if "error" in entry:
                summary["errors"].append(entry)
                continue
            name = entry["meta"]["name"]
            _LOADED[name] = entry

            # Upsert plugins row
            row = conn.execute("SELECT id, enabled FROM plugins WHERE name=?", (name,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE plugins SET version=?, description=?, updated_at=?, last_error=NULL WHERE id=?",
                    (entry["meta"].get("version"), entry["meta"].get("description"), now, row["id"]),
                )
                pid = row["id"]
            else:
                conn.execute(
                    """INSERT INTO plugins (name, version, description, enabled,
                                            installed_at, updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (name, entry["meta"].get("version"),
                     entry["meta"].get("description"), 1, now, now),
                )
                pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Re-record hook rows for this plug-in
            conn.execute("DELETE FROM plugin_hooks WHERE plugin_id=?", (pid,))
            for hook_name in entry["hooks"]:
                conn.execute(
                    "INSERT INTO plugin_hooks (plugin_id, hook_name, priority, created_at) "
                    "VALUES (?,?,?,?)",
                    (pid, hook_name, 100, now),
                )
            summary["loaded"].append({
                "name": name, "version": entry["meta"].get("version"),
                "hooks": list(entry["hooks"].keys()),
            })
    return summary


# ---------- enable/disable ----------

def list_(ctx: ServiceContext) -> list[dict]:
    from .contacts import ServiceError
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute("SELECT * FROM plugins ORDER BY name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["hooks"] = [h[0] for h in conn.execute(
                "SELECT hook_name FROM plugin_hooks WHERE plugin_id=?", (r["id"],),
            )]
            d["loaded"] = d["name"] in _LOADED
            out.append(d)
    return out


def enable(ctx: ServiceContext, plugin_id: int) -> dict:
    from .contacts import ServiceError
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "plug-in enable requires admin")
    with db() as conn:
        row = conn.execute("SELECT name FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        if not row:
            raise ServiceError("PLUGIN_NOT_FOUND", f"plug-in {plugin_id} not found")
        conn.execute("UPDATE plugins SET enabled=1, updated_at=? WHERE id=?",
                     (int(time.time()), plugin_id))
        audit.log(conn, ctx, action="plugin.enabled", object_type="plugin",
                  object_id=plugin_id, after={"name": row["name"]})
    return {"id": plugin_id, "enabled": True}


def disable(ctx: ServiceContext, plugin_id: int) -> dict:
    from .contacts import ServiceError
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "plug-in disable requires admin")
    with db() as conn:
        row = conn.execute("SELECT name FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        if not row:
            raise ServiceError("PLUGIN_NOT_FOUND", f"plug-in {plugin_id} not found")
        conn.execute("UPDATE plugins SET enabled=0, updated_at=? WHERE id=?",
                     (int(time.time()), plugin_id))
        audit.log(conn, ctx, action="plugin.disabled", object_type="plugin",
                  object_id=plugin_id, after={"name": row["name"]})
    return {"id": plugin_id, "enabled": False}


# ---------- hook dispatch ----------

def _enabled_names() -> set[str]:
    with db() as conn:
        rows = conn.execute("SELECT name FROM plugins WHERE enabled=1").fetchall()
    return {r[0] for r in rows}


def dispatch(hook_name: str, *args, **kwargs) -> list[Any]:
    """Call every loaded+enabled plug-in's matching hook. Returns list of results
    (None for void hooks). Errors are caught and logged to plugins.last_error
    so one bad plug-in can't crash the host."""
    if hook_name not in KNOWN_HOOKS:
        return []
    enabled = _enabled_names()
    results = []
    for name, entry in _LOADED.items():
        if name not in enabled:
            continue
        fn = entry["hooks"].get(hook_name)
        if not fn:
            continue
        try:
            results.append(fn(*args, **kwargs))
        except Exception:
            err = traceback.format_exc()
            try:
                with db() as conn:
                    conn.execute(
                        "UPDATE plugins SET last_error=?, updated_at=? WHERE name=?",
                        (err[-2000:], int(time.time()), name),
                    )
            except Exception:
                pass
    return results


def compute_fit_score_via_plugin(ctx, contact_id: int) -> Optional[tuple[int, list[dict]]]:
    """Special hook: if any enabled plug-in implements compute_fit_score, the
    FIRST one to return non-None wins. Returns (score, evidence) or None.
    """
    enabled = _enabled_names()
    for name, entry in _LOADED.items():
        if name not in enabled:
            continue
        fn = entry["hooks"].get("compute_fit_score")
        if not fn:
            continue
        try:
            result = fn(ctx, contact_id)
        except Exception:
            err = traceback.format_exc()
            try:
                with db() as conn:
                    conn.execute(
                        "UPDATE plugins SET last_error=?, updated_at=? WHERE name=?",
                        (err[-2000:], int(time.time()), name),
                    )
            except Exception:
                pass
            continue
        if result is not None:
            return result
    return None
