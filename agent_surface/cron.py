"""Cron-scheduled jobs.

At v0 this is a stub. Real cron jobs land in v1. Defined as a Python list so
external schedulers (Windows Task Scheduler, systemd timers, GitHub Actions)
can introspect what jobs exist.

To run all due jobs once:
    python -m agent_surface.cron run-due

When implemented, jobs will:
- Have a unique name + cron expression + service-layer call
- Use a `ServiceContext(role='system', scope='admin', surface='cron')`
- Be idempotent (safe to run twice in a minute)
- Log to audit_log like any other surface
"""

JOBS = [
    # Example shape (not active at v0):
    # {
    #     "name": "weekly_relationship_digest",
    #     "schedule": "0 9 * * MON",
    #     "fn": "backend.services.reports.send_weekly_digest",
    #     "description": "Email each user a summary of last week's activity",
    # },
]


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for j in JOBS:
            print(f"  {j['name']}  ({j['schedule']})  {j.get('description','')}")
        if not JOBS:
            print("(no cron jobs defined at v0)")
        return
    print("v0: no cron jobs implemented. Use `python -m agent_surface.cron list` to see definitions.")


if __name__ == "__main__":
    main()
