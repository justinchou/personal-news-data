#!/usr/bin/env python3
"""Run one session-aware refresh. Usage: python scripts/fetch_live.py [--force all|us_equity,crypto,...] [--status]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livedata import live, market_calendar as mc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", default="", help="comma-separated group ids, or 'all'")
    ap.add_argument("--status", action="store_true", help="print market open/closed now and exit")
    a = ap.parse_args()
    if a.status:
        for s in mc.snapshot():
            when = f"closes {s['next_close']['bj']}" if s["next_close"] else (f"opens {s['next_open']['bj']}" if s["next_open"] else "24/7")
            print(f"{s['market']:<11} {'OPEN ' if s['open'] else 'closed'} {when} (Beijing time) calendar_valid={s['calendar_valid']}")
        return 0
    force = {x.strip() for x in a.force.split(",") if x.strip()} or None
    doc, done = live.run(force=force)
    changed = live.write(doc)
    errs = {g: m["error"] for g, m in doc.get("groups", {}).items() if m.get("error")}
    print(json.dumps({"fetched": done, "changed": changed, "errors": errs,
                      "missing": {g: m.get("missing") for g, m in doc.get("groups", {}).items() if m.get("missing")}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
