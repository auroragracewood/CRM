"""CRM CLI — local operator surface.

The CLI calls the service layer DIRECTLY against the local SQLite database.
It is NOT a network client. Run it on the same machine that has the CRM repo
and its `crm.db` file. Remote automation should use REST or MCP.

Usage:
  python -m agent_surface.cli contact create --name "..." --email "..."
  python -m agent_surface.cli contact list [--q ...]
  python -m agent_surface.cli contact get --id 1
  python -m agent_surface.cli contact update --id 1 --name "..."
  python -m agent_surface.cli contact delete --id 1
  python -m agent_surface.cli backup create [--out <path>]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.context import ServiceContext  # noqa: E402
from backend.db import DB_PATH, db  # noqa: E402
from backend.services import (  # noqa: E402
    contacts as contacts_service,
    companies as companies_service,
    interactions as interactions_service,
    notes as notes_service,
    tags as tags_service,
    consent as consent_service,
)
from backend.services.contacts import ServiceError  # noqa: E402


def _resolve_user(args) -> tuple[int, str]:
    """Pick an acting user: --as-user-id, --as-email, or fall back to first admin."""
    with db() as conn:
        if args.as_user_id:
            row = conn.execute(
                "SELECT id, role FROM users WHERE id = ?", (args.as_user_id,)
            ).fetchone()
        elif args.as_email:
            row = conn.execute(
                "SELECT id, role FROM users WHERE email = ?", (args.as_email.lower().strip(),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, role FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
            ).fetchone()
    if not row:
        print("ERROR: no matching user found (and no admin to fall back to). Run `python setup.py`.", file=sys.stderr)
        sys.exit(2)
    return row["id"], row["role"]


def _ctx(args, role: str, user_id: int) -> ServiceContext:
    scope = "admin" if role == "admin" else ("read" if role == "readonly" else "write")
    return ServiceContext(
        user_id=user_id, role=role, scope=scope, surface="cli",
    )


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


# ----- commands -----

def cmd_contact_create(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    payload = {
        "full_name": args.name,
        "email": args.email,
        "phone": args.phone,
        "title": args.title,
        "location": args.location,
    }
    try:
        contact = contacts_service.create(ctx, payload)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {
            "code": e.code, "message": e.message, "details": e.details
        }}, indent=2))
        sys.exit(1)
    _print({"ok": True, "contact": contact})


def cmd_contact_get(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    try:
        contact = contacts_service.get(ctx, args.id)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}, indent=2))
        sys.exit(1)
    _print({"ok": True, "contact": contact})


def cmd_contact_list(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    result = contacts_service.list_(ctx, limit=args.limit, offset=args.offset, q=args.q)
    _print({"ok": True, **result})


def cmd_contact_update(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    payload = {k: v for k, v in {
        "full_name": args.name,
        "email": args.email,
        "phone": args.phone,
        "title": args.title,
        "location": args.location,
    }.items() if v is not None}
    if not payload:
        print("ERROR: provide at least one field to update", file=sys.stderr)
        sys.exit(2)
    try:
        contact = contacts_service.update(ctx, args.id, payload)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}, indent=2))
        sys.exit(1)
    _print({"ok": True, "contact": contact})


def cmd_contact_delete(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    try:
        result = contacts_service.delete(ctx, args.id)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}, indent=2))
        sys.exit(1)
    _print({"ok": True, **result})


def _handle(args, fn, *fnargs, **fnkwargs):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    try:
        out = fn(ctx, *fnargs, **fnkwargs)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {
            "code": e.code, "message": e.message, "details": e.details
        }}, indent=2, default=str))
        sys.exit(1)
    return out


# ----- companies -----

def cmd_company_create(args):
    out = _handle(args, companies_service.create, {
        "name": args.name, "slug": args.slug, "website": args.website,
        "domain": args.domain, "industry": args.industry, "location": args.location,
    })
    _print({"ok": True, "company": out})


def cmd_company_get(args):
    out = _handle(args, companies_service.get, args.id)
    _print({"ok": True, "company": out})


def cmd_company_list(args):
    out = _handle(args, companies_service.list_, limit=args.limit, offset=args.offset, q=args.q)
    _print({"ok": True, **out})


def cmd_company_update(args):
    payload = {k: v for k, v in {
        "name": args.name, "slug": args.slug, "website": args.website,
        "domain": args.domain, "industry": args.industry, "location": args.location,
    }.items() if v is not None}
    if not payload:
        print("ERROR: provide at least one field to update", file=sys.stderr)
        sys.exit(2)
    out = _handle(args, companies_service.update, args.id, payload)
    _print({"ok": True, "company": out})


def cmd_company_delete(args):
    out = _handle(args, companies_service.delete, args.id)
    _print({"ok": True, **out})


# ----- interactions -----

def cmd_interaction_log(args):
    payload = {
        "type": args.type,
        "contact_id": args.contact_id,
        "company_id": args.company_id,
        "title": args.title,
        "body": args.body,
        "channel": args.channel,
        "source": args.source,
    }
    out = _handle(args, interactions_service.log, payload)
    _print({"ok": True, "interaction": out})


def cmd_interaction_list(args):
    if args.contact_id:
        items = _handle(args, interactions_service.list_for_contact, args.contact_id,
                        limit=args.limit, offset=args.offset)
    elif args.company_id:
        items = _handle(args, interactions_service.list_for_company, args.company_id,
                        limit=args.limit, offset=args.offset)
    else:
        print("ERROR: provide --contact-id or --company-id", file=sys.stderr)
        sys.exit(2)
    _print({"ok": True, "items": items})


# ----- notes -----

def cmd_note_create(args):
    out = _handle(
        args, notes_service.create,
        contact_id=args.contact_id, company_id=args.company_id,
        body=args.body, visibility=args.visibility,
    )
    _print({"ok": True, "note": out})


def cmd_note_list(args):
    out = _handle(args, notes_service.list_for_contact, args.contact_id)
    _print({"ok": True, "items": out})


def cmd_note_reveal(args):
    out = _handle(args, notes_service.reveal_private, args.id)
    _print({"ok": True, "note": out})


# ----- tags -----

def cmd_tag_create(args):
    out = _handle(args, tags_service.create, args.name, color=args.color, scope=args.scope)
    _print({"ok": True, "tag": out})


def cmd_tag_list(args):
    out = _handle(args, tags_service.list_all)
    _print({"ok": True, "items": out})


def cmd_tag_attach(args):
    out = _handle(args, tags_service.attach,
                  tag_id=args.tag_id, contact_id=args.contact_id, company_id=args.company_id)
    _print({"ok": True, **out})


# ----- consent -----

def cmd_consent_record(args):
    out = _handle(args, consent_service.record,
                  args.contact_id, args.channel, args.status,
                  source=args.source, proof=args.proof)
    _print({"ok": True, "consent": out})


def cmd_consent_list(args):
    out = _handle(args, consent_service.list_for_contact, args.contact_id)
    _print({"ok": True, "items": out})


def cmd_backup_create(args):
    src = Path(DB_PATH)
    if not src.exists():
        print(f"ERROR: no database at {src}", file=sys.stderr)
        sys.exit(2)
    if args.out:
        dest = Path(args.out)
    else:
        backups_dir = ROOT / "backups"
        backups_dir.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = backups_dir / f"crm-{ts}.db"

    # Use SQLite's online backup API so live writes don't corrupt the snapshot.
    src_conn = sqlite3.connect(str(src))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()
    print(json.dumps({"ok": True, "backup": str(dest), "size_bytes": dest.stat().st_size}, indent=2))


# ----- argparse -----

def build_parser():
    p = argparse.ArgumentParser(prog="crm-cli", description="CRM local operator CLI")
    p.add_argument("--as-user-id", type=int, help="Act as this user id")
    p.add_argument("--as-email", help="Act as this user (by email)")
    sub = p.add_subparsers(dest="group", required=True)

    contact = sub.add_parser("contact", help="Contact commands")
    csub = contact.add_subparsers(dest="action", required=True)

    cc = csub.add_parser("create")
    cc.add_argument("--name", "--full-name", dest="name")
    cc.add_argument("--email")
    cc.add_argument("--phone")
    cc.add_argument("--title")
    cc.add_argument("--location")
    cc.set_defaults(func=cmd_contact_create)

    cg = csub.add_parser("get")
    cg.add_argument("--id", type=int, required=True)
    cg.set_defaults(func=cmd_contact_get)

    cl = csub.add_parser("list")
    cl.add_argument("--q", default=None, help="Search query (name or email LIKE)")
    cl.add_argument("--limit", type=int, default=50)
    cl.add_argument("--offset", type=int, default=0)
    cl.set_defaults(func=cmd_contact_list)

    cu = csub.add_parser("update")
    cu.add_argument("--id", type=int, required=True)
    cu.add_argument("--name", "--full-name", dest="name")
    cu.add_argument("--email")
    cu.add_argument("--phone")
    cu.add_argument("--title")
    cu.add_argument("--location")
    cu.set_defaults(func=cmd_contact_update)

    cd = csub.add_parser("delete")
    cd.add_argument("--id", type=int, required=True)
    cd.set_defaults(func=cmd_contact_delete)

    # company
    company = sub.add_parser("company", help="Company commands")
    cosub = company.add_subparsers(dest="action", required=True)
    coc = cosub.add_parser("create"); coc.add_argument("--name", required=True)
    coc.add_argument("--slug"); coc.add_argument("--website"); coc.add_argument("--domain")
    coc.add_argument("--industry"); coc.add_argument("--location")
    coc.set_defaults(func=cmd_company_create)
    cog = cosub.add_parser("get"); cog.add_argument("--id", type=int, required=True); cog.set_defaults(func=cmd_company_get)
    col = cosub.add_parser("list"); col.add_argument("--q"); col.add_argument("--limit", type=int, default=50); col.add_argument("--offset", type=int, default=0); col.set_defaults(func=cmd_company_list)
    cou = cosub.add_parser("update"); cou.add_argument("--id", type=int, required=True)
    cou.add_argument("--name"); cou.add_argument("--slug"); cou.add_argument("--website")
    cou.add_argument("--domain"); cou.add_argument("--industry"); cou.add_argument("--location")
    cou.set_defaults(func=cmd_company_update)
    cod = cosub.add_parser("delete"); cod.add_argument("--id", type=int, required=True); cod.set_defaults(func=cmd_company_delete)

    # interaction
    interaction = sub.add_parser("interaction", help="Interaction (timeline) commands")
    isub = interaction.add_subparsers(dest="action", required=True)
    il = isub.add_parser("log")
    il.add_argument("--type", required=True,
                    choices=["email","call","meeting","form_submission","page_view","note_system","system"])
    il.add_argument("--contact-id", dest="contact_id", type=int)
    il.add_argument("--company-id", dest="company_id", type=int)
    il.add_argument("--title"); il.add_argument("--body"); il.add_argument("--channel"); il.add_argument("--source")
    il.set_defaults(func=cmd_interaction_log)
    ilist = isub.add_parser("list")
    ilist.add_argument("--contact-id", dest="contact_id", type=int)
    ilist.add_argument("--company-id", dest="company_id", type=int)
    ilist.add_argument("--limit", type=int, default=50); ilist.add_argument("--offset", type=int, default=0)
    ilist.set_defaults(func=cmd_interaction_list)

    # note
    note = sub.add_parser("note", help="Note commands")
    nsub = note.add_subparsers(dest="action", required=True)
    nc = nsub.add_parser("create")
    nc.add_argument("--contact-id", dest="contact_id", type=int)
    nc.add_argument("--company-id", dest="company_id", type=int)
    nc.add_argument("--body", required=True)
    nc.add_argument("--visibility", default="team", choices=["public","team","private"])
    nc.set_defaults(func=cmd_note_create)
    nl = nsub.add_parser("list"); nl.add_argument("--contact-id", dest="contact_id", type=int, required=True); nl.set_defaults(func=cmd_note_list)
    nr = nsub.add_parser("reveal"); nr.add_argument("--id", type=int, required=True); nr.set_defaults(func=cmd_note_reveal)

    # tag
    tag = sub.add_parser("tag", help="Tag commands")
    tsub = tag.add_subparsers(dest="action", required=True)
    tc = tsub.add_parser("create"); tc.add_argument("--name", required=True)
    tc.add_argument("--color"); tc.add_argument("--scope", default="any", choices=["contact","company","any"])
    tc.set_defaults(func=cmd_tag_create)
    tl = tsub.add_parser("list"); tl.set_defaults(func=cmd_tag_list)
    ta = tsub.add_parser("attach"); ta.add_argument("--tag-id", dest="tag_id", type=int, required=True)
    ta.add_argument("--contact-id", dest="contact_id", type=int)
    ta.add_argument("--company-id", dest="company_id", type=int)
    ta.set_defaults(func=cmd_tag_attach)

    # consent
    consent = sub.add_parser("consent", help="Consent commands")
    csub2 = consent.add_subparsers(dest="action", required=True)
    cr = csub2.add_parser("record")
    cr.add_argument("--contact-id", dest="contact_id", type=int, required=True)
    cr.add_argument("--channel", required=True)
    cr.add_argument("--status", required=True, choices=["granted","withdrawn","unknown"])
    cr.add_argument("--source"); cr.add_argument("--proof")
    cr.set_defaults(func=cmd_consent_record)
    clist = csub2.add_parser("list"); clist.add_argument("--contact-id", dest="contact_id", type=int, required=True); clist.set_defaults(func=cmd_consent_list)

    # backup
    backup = sub.add_parser("backup", help="Backup commands")
    bsub = backup.add_subparsers(dest="action", required=True)
    bc = bsub.add_parser("create")
    bc.add_argument("--out", help="Optional path for backup file")
    bc.set_defaults(func=cmd_backup_create)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
