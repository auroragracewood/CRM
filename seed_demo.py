"""Seed the CRM with realistic demo data so every page has content to show.

Idempotent-ish: contacts with the same email already on file will be skipped
(partial unique index protects against duplicates). The script reports what
was created vs skipped at the end.

Run:  python seed_demo.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.services import (  # noqa: E402
    plugins as plug, contacts, companies, interactions, notes, tags,
    deals, tasks, pipelines, forms, scoring, consent, portals,
)
from backend.services.contacts import ServiceError  # noqa: E402
from backend.context import system_context  # noqa: E402


def _try(label, fn):
    try:
        return fn(), None
    except ServiceError as e:
        return None, f"{label}: {e.code} {e.message}"


def main():
    plug.reload_all()
    ctx = system_context()
    NOW = int(time.time())
    created = {"companies": 0, "contacts": 0, "interactions": 0, "deals": 0,
               "tasks": 0, "notes": 0, "forms": 0, "tokens": 0, "scored": 0}
    skipped = []

    # --- Companies ---
    co_specs = [
        ("Acme Roastery", "acme.coffee", "food & beverage", "Vancouver, BC"),
        ("Blue River Media", "blueriver.media", "marketing", "Toronto, ON"),
        ("Hammer Build Co.", "hammerbuild.example", "construction", "Abbotsford, BC"),
    ]
    co_map = {}
    for name, domain, industry, location in co_specs:
        out, err = _try(f"company {name}", lambda n=name, d=domain, i=industry, l=location:
                        companies.create(ctx, {"name": n, "domain": d, "industry": i, "location": l}))
        if out:
            co_map[name] = out
            created["companies"] += 1
        else:
            skipped.append(err)

    acme = co_map.get("Acme Roastery") or {}
    blue = co_map.get("Blue River Media") or {}
    hammer = co_map.get("Hammer Build Co.") or {}

    # --- Contacts ---
    contact_specs = [
        ("Maya Sato", "maya@blueriver.media", "+1 604-555-0188", "Marketing Director",
         blue.get("id"), "Curates editorial campaigns for boutique food brands. Prefers email over phone.",
         "email", "Toronto, ON", "manual", "https://linkedin.com/in/mayasato"),
        ("Greg Johnson", "greg@hammerbuild.example", "+1 778-555-0233", "Owner",
         hammer.get("id"), "Runs a third-gen residential build firm. Wants public art tied into commercial lobbies.",
         "phone", "Abbotsford, BC", "form:contact-us", None),
        ("Sara Patel", "sara@acme.coffee", "+1 604-555-0911", "Brand Manager",
         acme.get("id"), "Re-doing their flagship cafe brand. Strong opinions on copper.",
         "email", "Vancouver, BC", "form:contact-us", None),
        ("Lin Wei", "lin.wei@example.local", None, "Public Art Advisor",
         None, "City-of-Vancouver consultant for outdoor works.",
         None, "Vancouver, BC", "referral", None),
        ("Tom Reeves", "tom@solo.example", None, "Architect",
         None, "Solo practice. Asked about feasibility studies for bronze atriums.",
         "email", None, "manual", None),
    ]
    c_map = {}
    for name, email, phone, title, cid, about, pc, loc, src, linked in contact_specs:
        payload = {"full_name": name, "email": email, "phone": phone, "title": title,
                   "company_id": cid, "about": about, "preferred_channel": pc,
                   "location": loc, "source": src, "linkedin_url": linked}
        payload = {k: v for k, v in payload.items() if v is not None}
        out, err = _try(f"contact {name}", lambda p=payload: contacts.create(ctx, p))
        if out:
            c_map[name] = out
            created["contacts"] += 1
        else:
            skipped.append(err)

    maya = c_map.get("Maya Sato") or {}
    greg = c_map.get("Greg Johnson") or {}
    sara = c_map.get("Sara Patel") or {}
    lin = c_map.get("Lin Wei") or {}
    tom = c_map.get("Tom Reeves") or {}

    # --- Tags ---
    vip, _ = _try("tag vip", lambda: tags.create(ctx, "vip", color="#c47a4a", scope="contact"))
    refsrc, _ = _try("tag referral", lambda: tags.create(ctx, "referral-source", color="#738c5e", scope="contact"))
    if vip and maya.get("id"):
        tags.attach(ctx, tag_id=vip["id"], contact_id=maya["id"])
    if refsrc and maya.get("id"):
        tags.attach(ctx, tag_id=refsrc["id"], contact_id=maya["id"])

    # --- Pipeline + deals ---
    pipe_out, err = _try("pipeline Q4 Sales", lambda: pipelines.create_from_template(ctx, "Q4 Sales", "sales"))
    if err:
        skipped.append(err)
    if pipe_out:
        stage_ids = [s["id"] for s in pipe_out["stages"]]
        deal_specs = [
            ("Acme cafe rebrand", stage_ids[2], sara.get("id"), acme.get("id"), 1800000, 60),
            ("Hammer lobby copper installation", stage_ids[3], greg.get("id"), hammer.get("id"), 4500000, 70),
            ("Blue River editorial sponsor", stage_ids[0], maya.get("id"), blue.get("id"), 1200000, 25),
        ]
        for title, sid, cid, comid, val, prob in deal_specs:
            if not cid:
                continue
            out, e = _try(f"deal {title}", lambda t=title, s=sid, c=cid, co=comid, v=val, p=prob:
                          deals.create(ctx, {"title": t, "pipeline_id": pipe_out["id"], "stage_id": s,
                                              "contact_id": c, "company_id": co,
                                              "value_cents": v, "currency": "cad", "probability": p}))
            if out: created["deals"] += 1
            elif e: skipped.append(e)

    # --- Interactions (auto-tag plug-in fires on each) ---
    inter_specs = [
        (maya, "meeting", "Coffee chat",
         "Talked through their fall editorial calendar. They sponsor 3 emerging artists a year. Asked about bronze sculpture commissions."),
        (maya, "call", "Sponsor follow-up",
         "Will share the deck next week. Interested in copper-themed feature."),
        (greg, "meeting", "Site visit",
         "Walked the lobby. Wants a 2m copper signage piece and a feature wall. Budget Q2 install. Asked about portfolio of fabricated bronze."),
        (greg, "call", "Quote review",
         "Talked pricing. Comfortable with the range. Sending formal proposal."),
        (sara, "meeting", "Brand workshop",
         "Mood-boarded copper, walnut, terracotta palette. Brand wants industrial-warm feel."),
        (sara, "email", "Followup",
         "Sent over reference images. Patios and exterior signage discussed."),
        (lin, "call", "Intro from Maya",
         "Reviewing approval process for public art on commercial frontages. Permit-friendly designs preferred."),
        (tom, "email", "Feasibility note",
         "Asked about engineering feasibility of large bronze atrium feature."),
    ]
    for c, type_, title, body in inter_specs:
        if not c.get("id"):
            continue
        out, e = _try(f"interaction {title}", lambda c=c, t=type_, tt=title, b=body:
                      interactions.log(ctx, {"type": t, "contact_id": c["id"],
                                             "title": tt, "body": b}))
        if out: created["interactions"] += 1
        elif e: skipped.append(e)

    # --- Notes ---
    note_specs = [
        (maya, "Best contact window: weekday mornings PT.", "team"),
        (greg, "Loves seeing real fabrication shop work. Send him photos.", "team"),
        (sara, "Decision deadline: end of next month.", "team"),
    ]
    for c, body, vis in note_specs:
        if not c.get("id"): continue
        out, e = _try("note", lambda c=c, b=body, v=vis:
                      notes.create(ctx, contact_id=c["id"], body=b, visibility=v))
        if out: created["notes"] += 1

    # --- Consent ---
    for c in (maya, greg, sara):
        if c.get("id"):
            _try("consent", lambda c=c: consent.record(ctx, c["id"], "email", "granted", source="manual"))

    # --- Tasks ---
    task_specs = [
        ("Send Hammer formal proposal", greg, "high", 2),
        ("Share copper portfolio with Maya", maya, "normal", 5),
        ("Acme mood board v2", sara, "urgent", -1),   # overdue
        ("Tom feasibility memo", tom, "low", 14),
    ]
    for title, c, pri, days in task_specs:
        if not c.get("id"): continue
        payload = {"title": title, "contact_id": c["id"], "priority": pri,
                   "due_date": NOW + days * 86400}
        out, e = _try("task", lambda p=payload: tasks.create(ctx, p))
        if out: created["tasks"] += 1
        elif e: skipped.append(e)

    # --- Scoring ---
    for c in (maya, greg, sara, lin, tom):
        if c.get("id"):
            _try("scoring", lambda c=c: scoring.compute_for_contact(ctx, c["id"]))
            created["scored"] += 1

    # --- Portal token for Greg (so the demo includes self-service URL) ---
    if greg.get("id"):
        out, _ = _try("portal", lambda: portals.issue(ctx, greg["id"], scope="client",
                                                       label="Hammer project portal", expires_in_days=60))
        if out:
            created["tokens"] += 1

    # --- Form ---
    out, e = _try("form contact-us", lambda: forms.create(ctx, {
        "slug": "contact-us", "name": "Contact Us",
        "schema": {"fields": [
            {"key": "name",     "type": "text",     "label": "Name", "required": True},
            {"key": "email",    "type": "email",    "label": "Email", "required": True},
            {"key": "interest", "type": "select",   "label": "Interest",
             "options": ["signage", "sculpture", "consulting"]},
            {"key": "message",  "type": "textarea", "label": "Message"},
        ]},
        "routing": {"tags": ["lead", "form:contact-us"],
                    "interest_tag_prefix": "interest:",
                    "auto_create_contact": True, "match_by_email": True},
        "active": True,
    }))
    if out: created["forms"] += 1
    elif e: skipped.append(e)

    print("SEED COMPLETE")
    print(f"  created: {created}")
    if skipped:
        print(f"  skipped ({len(skipped)} — likely existed already):")
        for s in skipped[:10]:
            print(f"    {s}")


if __name__ == "__main__":
    main()
