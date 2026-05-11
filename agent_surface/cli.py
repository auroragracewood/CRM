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
    pipelines as pipelines_service,
    deals as deals_service,
    tasks as tasks_service,
    forms as forms_service,
    duplicates as duplicates_service,
    search as search_service,
    imports as imports_service,
    scoring as scoring_service,
    segments as segments_service,
    reports as reports_service,
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


# ----- pipelines -----

def cmd_pipeline_create(args):
    out = _handle(args, pipelines_service.create_pipeline, {
        "name": args.name, "type": args.type, "description": args.description,
    })
    _print({"ok": True, "pipeline": out})


def cmd_pipeline_from_template(args):
    out = _handle(args, pipelines_service.create_from_template, args.name, args.template)
    _print({"ok": True, "pipeline": out})


def cmd_pipeline_list(args):
    out = _handle(args, pipelines_service.list_pipelines, include_archived=args.include_archived)
    _print({"ok": True, "items": out})


def cmd_pipeline_get(args):
    out = _handle(args, pipelines_service.get_pipeline, args.id)
    _print({"ok": True, "pipeline": out})


def cmd_pipeline_add_stage(args):
    out = _handle(args, pipelines_service.add_stage, args.pipeline_id, args.name,
                  position=args.position, is_won=args.is_won, is_lost=args.is_lost)
    _print({"ok": True, "stage": out})


def cmd_pipeline_archive(args):
    out = _handle(args, pipelines_service.archive_pipeline, args.id)
    _print({"ok": True, **out})


# ----- deals -----

def cmd_deal_create(args):
    payload = {k: v for k, v in {
        "title": args.title,
        "pipeline_id": args.pipeline_id, "stage_id": args.stage_id,
        "contact_id": args.contact_id, "company_id": args.company_id,
        "value_cents": args.value_cents, "currency": args.currency,
        "probability": args.probability, "expected_close": args.expected_close,
        "status": args.status, "next_step": args.next_step,
        "assigned_to": args.assigned_to,
    }.items() if v is not None}
    out = _handle(args, deals_service.create, payload)
    _print({"ok": True, "deal": out})


def cmd_deal_get(args):
    out = _handle(args, deals_service.get, args.id)
    _print({"ok": True, "deal": out})


def cmd_deal_list(args):
    out = _handle(args, deals_service.list_,
                  pipeline_id=args.pipeline_id, stage_id=args.stage_id,
                  status=args.status, assigned_to=args.assigned_to,
                  contact_id=args.contact_id, company_id=args.company_id,
                  limit=args.limit, offset=args.offset)
    _print({"ok": True, **out})


def cmd_deal_update(args):
    payload = {k: v for k, v in {
        "title": args.title, "stage_id": args.stage_id, "status": args.status,
        "value_cents": args.value_cents, "currency": args.currency,
        "probability": args.probability, "expected_close": args.expected_close,
        "next_step": args.next_step, "assigned_to": args.assigned_to, "notes": args.notes,
    }.items() if v is not None}
    if not payload:
        print("ERROR: provide at least one field to update", file=sys.stderr); sys.exit(2)
    out = _handle(args, deals_service.update, args.id, payload)
    _print({"ok": True, "deal": out})


def cmd_deal_delete(args):
    out = _handle(args, deals_service.delete, args.id)
    _print({"ok": True, **out})


# ----- tasks -----

def cmd_task_create(args):
    payload = {k: v for k, v in {
        "title": args.title, "description": args.description,
        "contact_id": args.contact_id, "company_id": args.company_id,
        "deal_id": args.deal_id, "assigned_to": args.assigned_to,
        "due_date": args.due_date, "priority": args.priority,
    }.items() if v is not None}
    out = _handle(args, tasks_service.create, payload)
    _print({"ok": True, "task": out})


def cmd_task_get(args):
    out = _handle(args, tasks_service.get, args.id)
    _print({"ok": True, "task": out})


def cmd_task_list(args):
    out = _handle(args, tasks_service.list_,
                  status=args.status, assigned_to=args.assigned_to,
                  contact_id=args.contact_id, company_id=args.company_id,
                  deal_id=args.deal_id, overdue=args.overdue, due_before=args.due_before,
                  limit=args.limit, offset=args.offset)
    _print({"ok": True, **out})


def cmd_task_update(args):
    payload = {k: v for k, v in {
        "title": args.title, "description": args.description,
        "status": args.status, "priority": args.priority,
        "due_date": args.due_date, "assigned_to": args.assigned_to,
    }.items() if v is not None}
    if not payload:
        print("ERROR: provide at least one field to update", file=sys.stderr); sys.exit(2)
    out = _handle(args, tasks_service.update, args.id, payload)
    _print({"ok": True, "task": out})


def cmd_task_complete(args):
    out = _handle(args, tasks_service.complete, args.id)
    _print({"ok": True, "task": out})


def cmd_task_delete(args):
    out = _handle(args, tasks_service.delete, args.id)
    _print({"ok": True, **out})


# ----- search -----

def cmd_search(args):
    out = _handle(args, search_service.search, args.q,
                  kinds=([k.strip() for k in args.kinds.split(",") if k.strip()] if args.kinds else None),
                  limit=args.limit)
    _print({"ok": True, **out})


# ----- duplicates -----

def cmd_duplicates_find(args):
    s_list = [s.strip() for s in args.strategies.split(",") if s.strip()] if args.strategies else None
    out = _handle(args, duplicates_service.find, strategies=s_list, max_groups=args.max_groups)
    _print({"ok": True, **out})


def cmd_duplicates_merge(args):
    merge_ids = [int(x) for x in args.merge_ids.split(",") if x.strip()]
    out = _handle(args, duplicates_service.merge, keep_id=args.keep_id, merge_ids=merge_ids)
    _print({"ok": True, **out})


# ----- imports + exports -----

def cmd_import(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    csv_text = Path(args.csv).read_text(encoding="utf-8")
    fn = imports_service.import_contacts if args.kind == "contacts" else imports_service.import_companies
    try:
        out = fn(ctx, csv_text, dry_run=args.dry_run)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {
            "code": e.code, "message": e.message
        }}, indent=2)); sys.exit(1)
    _print({"ok": True, **out})


def cmd_export(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    try:
        out_stream = imports_service.export_csv(ctx, args.kind, include_deleted=args.include_deleted)
    except ServiceError as e:
        print(json.dumps({"ok": False, "error": {
            "code": e.code, "message": e.message
        }}, indent=2)); sys.exit(1)
    sink = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        for chunk in out_stream:
            sink.write(chunk)
    finally:
        if args.out:
            sink.close()
    if args.out:
        print(f"Wrote {args.out}", file=sys.stderr)


# ----- scoring -----

def cmd_score_contact(args):
    out = _handle(args, scoring_service.compute_for_contact, args.id)
    _print({"ok": True, **out})


def cmd_score_get(args):
    out = _handle(args, scoring_service.get_scores, args.id)
    _print({"ok": True, **out})


def cmd_score_recompute_all(args):
    out = _handle(args, scoring_service.compute_for_all, limit=args.limit)
    _print({"ok": True, **out})


def cmd_score_top(args):
    out = _handle(args, scoring_service.list_top, args.type,
                  limit=args.limit, min_score=args.min)
    _print({"ok": True, "items": out, "score_type": args.type})


# ----- segments -----

def cmd_segment_create_static(args):
    ids = [int(x) for x in args.contact_ids.split(",") if x.strip()]
    out = _handle(args, segments_service.create_static,
                  name=args.name, slug=args.slug, contact_ids=ids)
    _print({"ok": True, "segment": out})


def cmd_segment_create_dynamic(args):
    if args.rules.startswith("@"):
        import json
        rules = json.loads(Path(args.rules[1:]).read_text(encoding="utf-8"))
    else:
        import json
        rules = json.loads(args.rules)
    out = _handle(args, segments_service.create_dynamic,
                  name=args.name, slug=args.slug, rules=rules)
    _print({"ok": True, "segment": out})


def cmd_segment_list(args):
    out = _handle(args, segments_service.list_)
    _print({"ok": True, "items": out})


def cmd_segment_members(args):
    out = _handle(args, segments_service.list_members, args.id,
                  limit=args.limit, offset=args.offset)
    _print({"ok": True, **out})


def cmd_segment_evaluate(args):
    out = _handle(args, segments_service.evaluate, args.id)
    _print({"ok": True, **out})


def cmd_segment_delete(args):
    out = _handle(args, segments_service.delete, args.id)
    _print({"ok": True, **out})


# ----- reports -----

def cmd_report_list(args):
    _print({"ok": True, "items": reports_service.list_reports()})


def cmd_report_run(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    import json as _json
    kw = _json.loads(args.params) if args.params else {}
    try:
        out = reports_service.run(ctx, args.name, **kw)
    except ServiceError as e:
        print(_json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}, indent=2))
        sys.exit(1)
    _print({"ok": True, **out})


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

    # pipeline
    pipeline = sub.add_parser("pipeline", help="Pipeline commands")
    psub = pipeline.add_subparsers(dest="action", required=True)
    pc = psub.add_parser("create")
    pc.add_argument("--name", required=True); pc.add_argument("--type", required=True)
    pc.add_argument("--description")
    pc.set_defaults(func=cmd_pipeline_create)
    pft = psub.add_parser("from-template")
    pft.add_argument("--name", required=True)
    pft.add_argument("--template", required=True, choices=list(pipelines_service.TEMPLATES))
    pft.set_defaults(func=cmd_pipeline_from_template)
    pl = psub.add_parser("list")
    pl.add_argument("--include-archived", dest="include_archived", action="store_true")
    pl.set_defaults(func=cmd_pipeline_list)
    pg = psub.add_parser("get"); pg.add_argument("--id", type=int, required=True); pg.set_defaults(func=cmd_pipeline_get)
    pas = psub.add_parser("add-stage")
    pas.add_argument("--pipeline-id", dest="pipeline_id", type=int, required=True)
    pas.add_argument("--name", required=True)
    pas.add_argument("--position", type=int)
    pas.add_argument("--is-won", dest="is_won", action="store_true")
    pas.add_argument("--is-lost", dest="is_lost", action="store_true")
    pas.set_defaults(func=cmd_pipeline_add_stage)
    pa = psub.add_parser("archive"); pa.add_argument("--id", type=int, required=True); pa.set_defaults(func=cmd_pipeline_archive)

    # deal
    deal = sub.add_parser("deal", help="Deal commands")
    dsub = deal.add_subparsers(dest="action", required=True)
    dc = dsub.add_parser("create")
    dc.add_argument("--title", required=True)
    dc.add_argument("--pipeline-id", dest="pipeline_id", type=int, required=True)
    dc.add_argument("--stage-id", dest="stage_id", type=int, required=True)
    dc.add_argument("--contact-id", dest="contact_id", type=int)
    dc.add_argument("--company-id", dest="company_id", type=int)
    dc.add_argument("--value-cents", dest="value_cents", type=int)
    dc.add_argument("--currency"); dc.add_argument("--probability", type=int)
    dc.add_argument("--expected-close", dest="expected_close", type=int)
    dc.add_argument("--status", choices=["open","won","lost","nurture"])
    dc.add_argument("--next-step", dest="next_step")
    dc.add_argument("--assigned-to", dest="assigned_to", type=int)
    dc.set_defaults(func=cmd_deal_create)
    dg = dsub.add_parser("get"); dg.add_argument("--id", type=int, required=True); dg.set_defaults(func=cmd_deal_get)
    dl = dsub.add_parser("list")
    dl.add_argument("--pipeline-id", dest="pipeline_id", type=int)
    dl.add_argument("--stage-id", dest="stage_id", type=int)
    dl.add_argument("--status", choices=["open","won","lost","nurture"])
    dl.add_argument("--assigned-to", dest="assigned_to", type=int)
    dl.add_argument("--contact-id", dest="contact_id", type=int)
    dl.add_argument("--company-id", dest="company_id", type=int)
    dl.add_argument("--limit", type=int, default=100); dl.add_argument("--offset", type=int, default=0)
    dl.set_defaults(func=cmd_deal_list)
    du = dsub.add_parser("update")
    du.add_argument("--id", type=int, required=True)
    du.add_argument("--title"); du.add_argument("--stage-id", dest="stage_id", type=int)
    du.add_argument("--status", choices=["open","won","lost","nurture"])
    du.add_argument("--value-cents", dest="value_cents", type=int)
    du.add_argument("--currency"); du.add_argument("--probability", type=int)
    du.add_argument("--expected-close", dest="expected_close", type=int)
    du.add_argument("--next-step", dest="next_step")
    du.add_argument("--assigned-to", dest="assigned_to", type=int)
    du.add_argument("--notes")
    du.set_defaults(func=cmd_deal_update)
    dd = dsub.add_parser("delete"); dd.add_argument("--id", type=int, required=True); dd.set_defaults(func=cmd_deal_delete)

    # task
    task = sub.add_parser("task", help="Task commands")
    tasub = task.add_subparsers(dest="action", required=True)
    tc = tasub.add_parser("create")
    tc.add_argument("--title", required=True); tc.add_argument("--description")
    tc.add_argument("--contact-id", dest="contact_id", type=int)
    tc.add_argument("--company-id", dest="company_id", type=int)
    tc.add_argument("--deal-id", dest="deal_id", type=int)
    tc.add_argument("--assigned-to", dest="assigned_to", type=int)
    tc.add_argument("--due-date", dest="due_date", type=int, help="unix seconds")
    tc.add_argument("--priority", choices=["low","normal","high","urgent"])
    tc.set_defaults(func=cmd_task_create)
    tg = tasub.add_parser("get"); tg.add_argument("--id", type=int, required=True); tg.set_defaults(func=cmd_task_get)
    tl = tasub.add_parser("list")
    tl.add_argument("--status", choices=["open","in_progress","done","cancelled"])
    tl.add_argument("--assigned-to", dest="assigned_to", type=int)
    tl.add_argument("--contact-id", dest="contact_id", type=int)
    tl.add_argument("--company-id", dest="company_id", type=int)
    tl.add_argument("--deal-id", dest="deal_id", type=int)
    tl.add_argument("--overdue", action="store_true")
    tl.add_argument("--due-before", dest="due_before", type=int)
    tl.add_argument("--limit", type=int, default=100); tl.add_argument("--offset", type=int, default=0)
    tl.set_defaults(func=cmd_task_list)
    tu = tasub.add_parser("update")
    tu.add_argument("--id", type=int, required=True)
    tu.add_argument("--title"); tu.add_argument("--description")
    tu.add_argument("--status", choices=["open","in_progress","done","cancelled"])
    tu.add_argument("--priority", choices=["low","normal","high","urgent"])
    tu.add_argument("--due-date", dest="due_date", type=int)
    tu.add_argument("--assigned-to", dest="assigned_to", type=int)
    tu.set_defaults(func=cmd_task_update)
    tdone = tasub.add_parser("complete"); tdone.add_argument("--id", type=int, required=True); tdone.set_defaults(func=cmd_task_complete)
    tdel = tasub.add_parser("delete"); tdel.add_argument("--id", type=int, required=True); tdel.set_defaults(func=cmd_task_delete)

    # search
    se = sub.add_parser("search", help="Global search (FTS5)")
    se.add_argument("--q", required=True, help="search query")
    se.add_argument("--kinds", default="", help="comma-separated: contact,company,interaction,note")
    se.add_argument("--limit", type=int, default=50)
    se.set_defaults(func=cmd_search)

    # duplicates
    dup = sub.add_parser("duplicates", help="Duplicate detection + merge")
    dsub2 = dup.add_subparsers(dest="action", required=True)
    df = dsub2.add_parser("find")
    df.add_argument("--strategies", default="", help="comma-separated: email,phone,name,name_company")
    df.add_argument("--max-groups", dest="max_groups", type=int, default=200)
    df.set_defaults(func=cmd_duplicates_find)
    dm = dsub2.add_parser("merge")
    dm.add_argument("--keep-id", dest="keep_id", type=int, required=True)
    dm.add_argument("--merge-ids", dest="merge_ids", required=True,
                    help="comma-separated contact ids to merge INTO keep-id")
    dm.set_defaults(func=cmd_duplicates_merge)

    # import
    imp = sub.add_parser("import", help="Bulk import from CSV")
    imp.add_argument("--kind", required=True, choices=["contacts", "companies"])
    imp.add_argument("--csv", required=True, help="path to CSV file")
    imp.add_argument("--dry-run", dest="dry_run", action="store_true")
    imp.set_defaults(func=cmd_import)

    # export
    exp = sub.add_parser("export", help="Bulk export to CSV")
    exp.add_argument("--kind", required=True,
                     choices=["contacts","companies","deals","tasks","interactions"])
    exp.add_argument("--out", help="optional output path; stdout if omitted")
    exp.add_argument("--include-deleted", dest="include_deleted", action="store_true")
    exp.set_defaults(func=cmd_export)

    # score
    score = sub.add_parser("score", help="Rule-based scoring")
    sc_sub = score.add_subparsers(dest="action", required=True)
    sc_c = sc_sub.add_parser("contact", help="Recompute scores for a contact")
    sc_c.add_argument("--id", type=int, required=True)
    sc_c.set_defaults(func=cmd_score_contact)
    sc_g = sc_sub.add_parser("get", help="Fetch persisted scores for a contact")
    sc_g.add_argument("--id", type=int, required=True)
    sc_g.set_defaults(func=cmd_score_get)
    sc_a = sc_sub.add_parser("recompute-all", help="Batch recompute (admin)")
    sc_a.add_argument("--limit", type=int)
    sc_a.set_defaults(func=cmd_score_recompute_all)
    sc_t = sc_sub.add_parser("top", help="Top contacts by score type")
    sc_t.add_argument("--type", default="opportunity",
                      choices=list(scoring_service.SCORE_TYPES))
    sc_t.add_argument("--min", type=int)
    sc_t.add_argument("--limit", type=int, default=20)
    sc_t.set_defaults(func=cmd_score_top)

    # segment
    seg = sub.add_parser("segment", help="Segments (static + dynamic)")
    sg_sub = seg.add_subparsers(dest="action", required=True)
    sg_cs = sg_sub.add_parser("create-static")
    sg_cs.add_argument("--name", required=True); sg_cs.add_argument("--slug", required=True)
    sg_cs.add_argument("--contact-ids", dest="contact_ids", required=True,
                       help="comma-separated contact ids")
    sg_cs.set_defaults(func=cmd_segment_create_static)
    sg_cd = sg_sub.add_parser("create-dynamic")
    sg_cd.add_argument("--name", required=True); sg_cd.add_argument("--slug", required=True)
    sg_cd.add_argument("--rules", required=True,
                       help='JSON rule tree, or @path/to/file.json')
    sg_cd.set_defaults(func=cmd_segment_create_dynamic)
    sg_l = sg_sub.add_parser("list"); sg_l.set_defaults(func=cmd_segment_list)
    sg_m = sg_sub.add_parser("members"); sg_m.add_argument("--id", type=int, required=True)
    sg_m.add_argument("--limit", type=int, default=200); sg_m.add_argument("--offset", type=int, default=0)
    sg_m.set_defaults(func=cmd_segment_members)
    sg_e = sg_sub.add_parser("evaluate"); sg_e.add_argument("--id", type=int, required=True)
    sg_e.set_defaults(func=cmd_segment_evaluate)
    sg_d = sg_sub.add_parser("delete"); sg_d.add_argument("--id", type=int, required=True)
    sg_d.set_defaults(func=cmd_segment_delete)

    # report
    rep = sub.add_parser("report", help="Pre-built reports")
    rsub = rep.add_subparsers(dest="action", required=True)
    rl = rsub.add_parser("list"); rl.set_defaults(func=cmd_report_list)
    rr = rsub.add_parser("run")
    rr.add_argument("--name", required=True, choices=list(reports_service.CATALOG))
    rr.add_argument("--params", default="", help='JSON kwargs (e.g. \'{"days":14}\')')
    rr.set_defaults(func=cmd_report_run)

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
