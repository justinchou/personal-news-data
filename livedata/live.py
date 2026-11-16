"""Build data/live/prices.json: session-aware, tiered refresh of watchlist quotes.

Each run (GitHub Actions, every 5 min) decides per group via market_calendar.should_fetch whether a fetch is due,
fetches due groups one after another, merges into the previous file, and only rewrites the file when something changed.
Failed groups keep the previous quotes and record the error.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import market_calendar as mc
from . import sources

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "live" / "prices.json"
BJ = mc.BJ


def symbols() -> dict:
    return yaml.safe_load((ROOT / "config" / "symbols.yml").read_text(encoding="utf-8"))


def load(path: Path = OUT) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _market_state(market: str, now: datetime) -> dict:
    st = mc.status(market, now)
    return {"open": st["open"], "next_open_bj": (st["next_open"] or {}).get("bj"), "next_close_bj": (st["next_close"] or {}).get("bj"),
            "calendar_valid": st["calendar_valid"], "name": st["name"]}


def run(now: datetime | None = None, *, force: set | None = None, prev: dict | None = None, fetch=None) -> tuple[dict, list[str]]:
    """Return (new document, list of fetched group ids). `fetch(source, codes, now)` is injectable for tests."""
    now = (now or datetime.now(timezone.utc)).astimezone(BJ)
    cfg = symbols()
    doc = json.loads(json.dumps(prev if prev is not None else load()))
    doc.setdefault("quotes", {})
    groups = doc.setdefault("groups", {})
    fetch = fetch or _fetch
    done = []
    for gid, g in cfg["groups"].items():
        market = g.get("market") or gid
        meta = groups.setdefault(gid, {})
        meta.update({"market": market, "source": g["source"], **_market_state(market, now)})
        last = meta.get("fetched_at")
        last_dt = datetime.fromisoformat(last) if last else None
        if not (force and (gid in force or "all" in force)) and not mc.should_fetch(market, now, last_dt):
            continue
        codes = [s["code"] for s in g["symbols"]]
        try:
            got = fetch(g["source"], codes, now)
        except Exception as e:  # keep previous quotes
            meta["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            meta["error_at"] = now.isoformat(timespec="seconds")
            continue
        meta["fetched_at"] = now.isoformat(timespec="seconds")
        meta.pop("error", None)
        meta.pop("error_at", None)
        missing = []
        for s in g["symbols"]:
            q = got.get(s["code"])
            if not q:
                missing.append(s["sym"])
                continue
            doc["quotes"][s["key"]] = {"sym": s["sym"], "group": gid, "market": market, "source": g["source"],
                                       "fetched_at": meta["fetched_at"], **{k: v for k, v in q.items() if k != "name"}}
        meta["missing"] = missing
        done.append(gid)
    srcs = cfg.get("sources") or {}
    doc["sources"] = srcs
    doc["schema"] = 1
    doc["repo"] = "https://github.com/justinchou/personal-news-data"
    doc["note"] = "Public quotes for personal-news-site; not investment advice. Times are Asia/Shanghai (+08:00)."
    if done:
        doc["generated_at"] = now.isoformat(timespec="seconds")
    return doc, done


def _fetch(source: str, codes: list[str], now: datetime) -> dict:
    if source == "tencent":
        return sources.tencent(codes)
    if source == "coinbase":
        return sources.coinbase(codes, now=now)
    raise ValueError(source)


def write(doc: dict, path: Path = OUT) -> bool:
    """Write if content changed; True when the file changed."""
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True
