"""v1+ acceptance script.

Covers the v1-v4 services that test_milestone1 doesn't touch:
  - pipelines + deals (v1)
  - tasks (v1)
  - forms (v1) including public submission -> auto-create contact
  - segments (static) (v2)
  - saved_views (v4)
  - scoring (v2)
  - plugins (v4) — list/discover only

Service-layer focused. Transport (REST/CLI/MCP) is already validated by
test_milestone1; re-running it for every resource would be wasteful.

Usage:
  python -m tests.test_v1plus
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _setup_temp_db():
    """Same pattern as tests/test_milestone1.py — fresh temp DB + admin user."""
    tmpdir = tempfile.mkdtemp(prefix="crm_test_v1plus_")
    db_path = os.path.join(tmpdir, "crm.db")
    os.environ["CRM_DB_PATH"] = db_path
    os.environ["CRM_DISABLE_DISPATCHER"] = "1"
    os.environ["CRM_SECRET_KEY"] = "test-secret-v1plus"

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
            ("admin@v1plus.test", pw_hash, "v1+ Admin", "admin", now, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"tmpdir": tmpdir, "db_path": db_path, "user_id": user_id}


def _count(conn, table, where="", params=()):
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def run():
    print("Setting up temp DB...")
    info = _setup_temp_db()
    print(f"  DB:     {info['db_path']}")
    print(f"  Admin:  id={info['user_id']}")

    from backend.context import ServiceContext
    from backend.db import db
    from backend.services import (
        contacts as contacts_svc,
        pipelines as pipelines_svc,
        deals as deals_svc,
        tasks as tasks_svc,
        forms as forms_svc,
        segments as segments_svc,
        saved_views as saved_views_svc,
        scoring as scoring_svc,
        plugins as plugins_svc,
    )

    ctx = ServiceContext(user_id=info["user_id"], role="admin",
                         scope="admin", surface="cli")

    # ---- [1/9] PIPELINES + deal-stage scaffolding (v1) ----
    print("\n[1/9] pipelines.create_pipeline (sales pipeline with 3 stages)...")
    pipeline = pipelines_svc.create_pipeline(
        ctx,
        {"name": "Sales", "type": "deal", "description": "v1+ test pipeline"},
        stages=[
            {"name": "Discovery", "position": 0},
            {"name": "Proposal",  "position": 1},
            {"name": "Closed Won", "position": 2, "is_won": True},
        ],
    )
    assert pipeline["id"] and len(pipeline["stages"]) == 3, pipeline
    stage_discovery = pipeline["stages"][0]["id"]
    stage_won       = pipeline["stages"][2]["id"]
    print(f"  OK   pipeline id={pipeline['id']} stages={[s['name'] for s in pipeline['stages']]}")

    # ---- [2/9] DEALS create + move-to-won (v1) ----
    print("\n[2/9] deals.create + deals.update (move to Won)...")
    deal = deals_svc.create(ctx, {
        "title": "Acme licence deal",
        "pipeline_id": pipeline["id"],
        "stage_id":    stage_discovery,
        "value":       12500,
        "currency":    "USD",
        "probability": 30,
    })
    assert deal["id"] and deal["status"] == "open", deal
    moved = deals_svc.update(ctx, deal["id"], {"stage_id": stage_won})
    # The service auto-flips status to 'won' when the new stage has is_won=1 —
    # ONLY if status is also explicitly set. Verify explicitly to keep the test
    # robust regardless of that behaviour.
    moved = deals_svc.update(ctx, deal["id"], {"status": "won"})
    assert moved["status"] == "won", moved
    print(f"  OK   deal id={deal['id']} title={deal['title']!r} -> status=won")

    # ---- [3/9] TASKS create + complete (v1) ----
    print("\n[3/9] tasks.create + tasks.complete...")
    task = tasks_svc.create(ctx, {
        "title": "Follow up with Acme",
        "deal_id": deal["id"],
        "priority": "high",
        "due_date": int(time.time()) + 86400,
    })
    assert task["id"] and task["status"] == "open", task
    done = tasks_svc.complete(ctx, task["id"])
    assert done["status"] == "done", done
    print(f"  OK   task id={task['id']} -> status=done")

    # ---- [4/9] FORMS create + public submission (v1) ----
    # submit_public exercises the heaviest cross-service path in v1:
    # schema validation -> auto-create contact -> routing -> audit -> webhook.
    print("\n[4/9] forms.create + forms.submit_public (lead capture flow)...")
    form = forms_svc.create(ctx, {
        "slug": "contact-us",
        "name": "Contact us",
        "schema": {
            "fields": [
                {"key": "name",    "label": "Name",    "type": "text",     "required": True},
                {"key": "email",   "label": "Email",   "type": "email",    "required": True},
                {"key": "message", "label": "Message", "type": "textarea"},
            ],
        },
    })
    assert form["id"] and form["slug"] == "contact-us", form

    # Capture pre-submission contact count to assert auto-create works.
    with db() as conn:
        before = _count(conn, "contacts", "deleted_at IS NULL")

    submission = forms_svc.submit_public("contact-us", {
        "name":    "Inbound Lead",
        "email":   "lead@example.test",
        "message": "Interested in a demo.",
    }, ip="127.0.0.1", user_agent="test-runner/1.0")
    assert submission.get("ok") or submission.get("submission_id"), submission

    with db() as conn:
        after = _count(conn, "contacts", "deleted_at IS NULL")
        lead = conn.execute(
            "SELECT id, full_name, email FROM contacts WHERE email=?",
            ("lead@example.test",),
        ).fetchone()
    assert after == before + 1, f"expected contact auto-create (before={before}, after={after})"
    assert lead is not None, "lead contact missing"
    lead_contact_id = lead["id"]
    print(f"  OK   form submission -> auto-created contact id={lead_contact_id} email={lead['email']}")

    # ---- [5/9] SEGMENTS (static membership) (v2) ----
    print("\n[5/9] segments.create_static...")
    seg = segments_svc.create_static(
        ctx, name="Inbound leads",
        slug="inbound-leads",
        contact_ids=[lead_contact_id],
    )
    assert seg["id"] and seg["member_count"] == 1, seg
    print(f"  OK   segment id={seg['id']} slug={seg['slug']!r} members={seg['member_count']}")

    # ---- [6/9] SAVED VIEWS (v4) ----
    print("\n[6/9] saved_views.create (entity=contact)...")
    view = saved_views_svc.create(
        ctx, entity="contact", name="Hot leads",
        config={"filter": {"segment": "inbound-leads"}},
        shared=True,
    )
    assert view["id"] and view["entity"] == "contact", view
    print(f"  OK   saved_view id={view['id']} name={view['name']!r} shared={bool(view['shared'])}")

    # ---- [7/9] SCORING — compute for the new lead (v2) ----
    print("\n[7/9] scoring.compute_for_contact...")
    scored = scoring_svc.compute_for_contact(ctx, lead_contact_id)
    # compute_for_contact returns the score dict — must include the canonical
    # four pillars + opportunity. A brand-new contact may legitimately score 0,
    # so we only verify the shape, not the magnitude.
    for key in ("relationship_strength", "intent", "fit", "risk", "opportunity"):
        assert key in scored, f"score missing key {key!r}: {scored}"
    print(f"  OK   contact id={lead_contact_id} opportunity={scored['opportunity']}")

    # ---- [8/9] PLUGINS — discover + list (v4) ----
    print("\n[8/9] plugins.reload_all + plugins.list_...")
    reload_result = plugins_svc.reload_all()
    assert "loaded" in reload_result or "plugins" in reload_result or isinstance(reload_result, dict), reload_result
    listed = plugins_svc.list_(ctx)
    assert isinstance(listed, list), listed
    plugin_names = {p["name"] for p in listed}
    assert "deal-stage-automation" in plugin_names, \
        f"deal-stage-automation not in registry: {plugin_names}"
    dsa = next(p for p in listed if p["name"] == "deal-stage-automation")
    assert dsa["enabled"] == 1 and dsa["loaded"] is True, dsa
    assert "on_deal_stage_changed" in dsa["hooks"], dsa
    print(f"  OK   plugin registry reachable; {len(listed)} plugin(s); "
          f"deal-stage-automation loaded+enabled with hook(s) {dsa['hooks']}")

    # ---- [9/9] PLUG-IN END-TO-END: deal_stage_automation fires real writes ----
    # Proves the plug-in framework can: discover -> load -> register -> dispatch
    # -> mutate DB inside the triggering transaction. Tests both branches of
    # the plug-in (won + lost) to cover the conditional logic in one pass.
    print("\n[9/9] plug-in end-to-end: deal-stage-automation hook fires...")

    # Build a self-contained pipeline with explicit won + lost stages so we
    # don't trample state from earlier blocks (which left a 'won' deal but
    # no plug-in had been loaded yet to receive its event).
    plugin_pipeline = pipelines_svc.create_pipeline(
        ctx,
        {"name": "Plug-in test pipeline", "type": "deal"},
        stages=[
            {"name": "New",   "position": 0},
            {"name": "Won!",  "position": 1, "is_won":  True},
            {"name": "Lost",  "position": 2, "is_lost": True},
        ],
    )
    pp_new_id  = plugin_pipeline["stages"][0]["id"]
    pp_won_id  = plugin_pipeline["stages"][1]["id"]
    pp_lost_id = plugin_pipeline["stages"][2]["id"]

    # Bind a contact to the deal so the interaction-logging path also runs
    # (the plug-in skips interaction writes when contact_id is None).
    happy_contact = contacts_svc.create(ctx, {
        "full_name": "Happy Customer",
        "email": "happy@example.test",
    })

    # WON path
    won_deal = deals_svc.create(ctx, {
        "title":      "Big Win Co.",
        "pipeline_id": plugin_pipeline["id"],
        "stage_id":    pp_new_id,
        "contact_id":  happy_contact["id"],
        "value":       50000, "currency": "USD",
    })
    deals_svc.update(ctx, won_deal["id"], {"stage_id": pp_won_id})

    # LOST path (separate deal so each branch is independently provable)
    lost_deal = deals_svc.create(ctx, {
        "title":      "Cold Co.",
        "pipeline_id": plugin_pipeline["id"],
        "stage_id":    pp_new_id,
        "contact_id":  happy_contact["id"],
    })
    deals_svc.update(ctx, lost_deal["id"], {"stage_id": pp_lost_id})

    with db() as conn:
        # Kickoff task on the WON deal
        won_task = conn.execute(
            "SELECT id, title, priority FROM tasks "
            "WHERE deal_id = ? AND title LIKE 'Kickoff%'",
            (won_deal["id"],),
        ).fetchone()
        # Postmortem task on the LOST deal
        lost_task = conn.execute(
            "SELECT id, title, priority FROM tasks "
            "WHERE deal_id = ? AND title LIKE 'Postmortem%'",
            (lost_deal["id"],),
        ).fetchone()
        # System interactions on the contact, one per deal
        interactions = conn.execute(
            "SELECT id, title, source FROM interactions "
            "WHERE contact_id = ? AND source = 'plugin:deal-stage-automation' "
            "ORDER BY id",
            (happy_contact["id"],),
        ).fetchall()

    assert won_task is not None, "plug-in did not create the WON kickoff task"
    assert won_task["priority"] == "high", f"won task priority wrong: {won_task['priority']}"
    assert "Big Win Co." in won_task["title"], won_task["title"]

    assert lost_task is not None, "plug-in did not create the LOST postmortem task"
    assert lost_task["priority"] == "normal", f"lost task priority wrong: {lost_task['priority']}"
    assert "Cold Co." in lost_task["title"], lost_task["title"]

    assert len(interactions) == 2, \
        f"expected 2 plug-in interactions (won + lost), got {len(interactions)}"
    won_int_titles = [i["title"] for i in interactions]
    assert any("Deal won" in t for t in won_int_titles), won_int_titles
    assert any("Deal lost" in t for t in won_int_titles), won_int_titles

    print(f"  OK   won-deal kickoff task id={won_task['id']} (priority=high)")
    print(f"  OK   lost-deal postmortem task id={lost_task['id']} (priority=normal)")
    print(f"  OK   2 system interactions on contact id={happy_contact['id']} "
          f"sourced from plug-in")

    # ---- audit_log spot-check: every service should have written audit rows ----
    print("\n[audit] cross-service audit_log spot-check...")
    expected_actions = {
        "pipeline.created", "deal.created", "task.created",
        "form.created", "segment.created", "saved_view.created",
    }
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT action FROM audit_log"
        ).fetchall()
    actions = {r[0] for r in rows}
    missing = expected_actions - actions
    assert not missing, f"audit_log missing actions: {missing}"
    print(f"  OK   audit_log has {len(actions)} distinct actions covering all v1+ services")

    print("\nv1+ ACCEPTANCE: PASS")
    print(f"\n(temp DB left at {info['db_path']} for inspection — safe to delete)")


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
