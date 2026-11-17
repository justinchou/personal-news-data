#!/usr/bin/env python3
"""Watchdog guard for the live-prices runner loop.

A watchdog run (cron, or a manual run with mode=watchdog) should start a loop only if no other run of
live-prices.yml is active. Every workflow_dispatch run counts as active (that is the loop chain). A schedule
run counts only if it is older than this one, so two overlapping watchdog runs cannot both stand down.

Usage (in Actions): python scripts/loop_guard.py  → writes start=true|false to $GITHUB_OUTPUT.
Needs GH_TOKEN (actions: read), GITHUB_REPOSITORY, GITHUB_RUN_ID.
"""
import json
import os
import subprocess
import sys

ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}


def blocking_runs(runs: list[dict], own_id: int) -> list[dict]:
    out = []
    for r in runs:
        if r.get("id") == own_id or r.get("status") not in ACTIVE:
            continue
        if r.get("event") == "schedule" and r.get("id", 0) > own_id:
            continue  # a newer watchdog stands down for us, not the other way round
        out.append(r)
    return out


def main() -> int:
    repo, own = os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_ID"])
    raw = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/workflows/live-prices.yml/runs?per_page=50"],
        check=True, capture_output=True, text=True).stdout
    blockers = blocking_runs(json.loads(raw).get("workflow_runs", []), own)
    for r in blockers:
        print(f"active: run {r['id']} {r['event']} {r['status']} {r.get('html_url', '')}")
    start = not blockers
    print("start loop" if start else "a loop is already active; watchdog exits")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        f.write(f"start={'true' if start else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
