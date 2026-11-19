#!/usr/bin/env python3
"""Watchdog guard for the live-prices collector.

A watchdog run (cron, or a manual run with mode=watchdog) should start a collector
only if no other run of live-prices.yml is active. Every workflow_dispatch run counts
as active (that is the collector chain). A schedule run counts only if it is older
than this one, so two overlapping watchdog runs cannot both stand down.

Prints one JSON object and, when data/live/prices.json is in the checkout, the
stored per-group freshness. Crypto older than its limit is the heartbeat: closed
equity sessions are not reported as failures.

Usage (in Actions): python scripts/loop_guard.py  → writes start=true|false to $GITHUB_OUTPUT.
Needs GH_TOKEN (actions: read), GITHUB_REPOSITORY, GITHUB_RUN_ID.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livedata.freshness import summarize_prices  # noqa: E402

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


def _note(text: str) -> None:
    print(f"::notice title=watchdog::{text}")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> int:
    repo, own = os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_ID"])
    raw = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/workflows/live-prices.yml/runs?per_page=50"],
        check=True, capture_output=True, text=True).stdout
    blockers = blocking_runs(json.loads(raw).get("workflow_runs", []), own)
    for r in blockers:
        print(f"active: run {r['id']} {r['event']} {r['status']} {r.get('html_url', '')}")
    start = not blockers
    if start:
        _note("no collector active; starting one")
    else:
        ids = ",".join(str(r.get("id")) for r in blockers)
        _note(f"collector active ({ids}); this run does not fetch")
    prices = ROOT / "data" / "live" / "prices.json"
    freshness = ""
    if prices.exists():
        try:
            doc = json.loads(prices.read_text(encoding="utf-8"))
            freshness = summarize_prices(doc, datetime.now(timezone.utc))
            print(freshness)
        except (OSError, ValueError) as e:
            freshness = f"prices.json unreadable: {type(e).__name__}"
            print(freshness)
    else:
        print("no prices.json in this checkout")
    report = {
        "role": "watchdog",
        "start": start,
        "blockers": [{"id": r.get("id"), "event": r.get("event"), "status": r.get("status")} for r in blockers],
    }
    print(json.dumps(report, ensure_ascii=False))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and freshness:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n```\n" + freshness + "\n```\n")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        f.write(f"start={'true' if start else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
