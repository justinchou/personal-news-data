#!/usr/bin/env python3
"""Run one session-aware refresh.

Usage: python scripts/fetch_live.py [--force all|us_equity,crypto,...] [--status]

Prints one JSON object. `summary` is the short form; `groups` is the cycle
(due/fetched/unchanged/closed/not_due/failed). Exit 0 when the script itself
ran, including when an origin failed and the previous quote was kept — that
failure is in the JSON and must still be committed.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livedata import freshness, live, market_calendar as mc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", default="", help="comma-separated group ids, or 'all'")
    ap.add_argument("--status", action="store_true", help="print market hours and stored freshness, then exit")
    a = ap.parse_args()
    if a.status:
        for s in mc.snapshot():
            when = f"closes {s['next_close']['bj']}" if s["next_close"] else (f"opens {s['next_open']['bj']}" if s["next_open"] else "24/7")
            print(f"{s['market']:<11} {'OPEN ' if s['open'] else 'closed'} {when} (Beijing time) calendar_valid={s['calendar_valid']}")
        doc = live.load()
        if doc:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).astimezone(mc.BJ)
            print(freshness.summarize_prices(doc, now))
        return 0
    force = {x.strip() for x in a.force.split(",") if x.strip()} or None
    doc, done, report = live.run(force=force)
    changed = live.write(doc)
    if changed:
        live.write_status(doc)
    history = False
    if "crypto" in done:
        history = live.append_history(doc) is not None
    errs = {g: m["error"] for g, m in doc.get("groups", {}).items() if m.get("error")}
    report["fetched"] = done
    report["changed"] = changed
    report["history"] = history
    report["errors"] = errs
    report["missing"] = {g: m.get("missing") for g, m in doc.get("groups", {}).items() if m.get("missing")}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
