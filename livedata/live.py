"""Build data/live/prices.json: session-aware, tiered refresh of watchlist quotes.

Each cycle decides per group whether a fetch is due. Due Coinbase groups run at
the same time as the Tencent chain (one sequence each). Tencent groups stay
one-at-a-time. Results are merged on the caller thread, then written atomically.

`generated_at` advances only when at least one group is observed successfully
(prices moved or not). A failed group keeps its previous quotes and fetched_at.
That old quote is not fresh just because generated_at moved for a sibling group.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import freshness
from . import market_calendar as mc
from . import sources

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "live" / "prices.json"
STATUS = ROOT / "data" / "live" / "status.json"
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
    return {
        "open": st["open"],
        "next_open_bj": (st["next_open"] or {}).get("bj"),
        "next_close_bj": (st["next_close"] or {}).get("bj"),
        "calendar_valid": st["calendar_valid"],
        "name": st["name"],
    }


def _market_view(doc: dict) -> dict:
    out = {}
    for gid, meta in (doc.get("groups") or {}).items():
        out[gid] = {k: meta.get(k) for k in ("open", "next_open_bj", "next_close_bj", "calendar_valid")}
    return out


def _one(plan: dict, fetch, now: datetime) -> dict:
    """Run on the fetch thread. Returns quotes or an error; never raises."""
    sources.begin_info()
    started = time.perf_counter()
    try:
        got = fetch(plan["source"], plan["codes"], now)
        err = None
    except Exception as e:  # origin failure: keep the previous quotes
        got = None
        err = e
    return {
        "got": got,
        "error": err,
        "duration_ms": int(round((time.perf_counter() - started) * 1000)),
        "http": sources.snapshot_info(),
    }


def _cooldown_until(doc: dict, source: str, now: datetime) -> datetime | None:
    raw = (doc.get("cooldowns") or {}).get(source)
    until = freshness.parse_time(raw if isinstance(raw, str) else None)
    if until is not None and until > now:
        return until
    return None


def _set_cooldown(doc: dict, source: str, until: datetime) -> str:
    """Remember the later of the stored cooldown and `until`. Returns the ISO time we will honor."""
    until = until.astimezone(BJ)
    cds = doc.setdefault("cooldowns", {})
    prev = freshness.parse_time(cds.get(source) if isinstance(cds.get(source), str) else None)
    if prev is not None and prev > until:
        until = prev
    iso = until.isoformat(timespec="seconds")
    cds[source] = iso
    return iso


def _expire_cooldowns(doc: dict, now: datetime) -> None:
    cds = doc.get("cooldowns")
    if not cds:
        return
    for source, raw in list(cds.items()):
        until = freshness.parse_time(raw if isinstance(raw, str) else None)
        if until is None or until <= now:
            cds.pop(source, None)
    if not cds:
        doc.pop("cooldowns", None)


def _deferred(until: datetime, now: datetime) -> dict:
    return {
        "got": None,
        "error": sources.RateLimited("cooldown", retry_at=until, wait_s=(until - now).total_seconds()),
        "duration_ms": 0,
        "http": {},
        "deferred": True,
    }


def _fetch_due(plans: list[dict], fetch, now: datetime, doc: dict) -> dict:
    """Coinbase (or any non-Tencent source) may overlap the Tencent chain. Tencent stays serial.

    A source still inside a server Retry-After is not called. After one rate-limit
    response, later groups of that same source are deferred too. The other source
    keeps going.
    """
    ready = []
    out = {}
    for plan in plans:
        until = _cooldown_until(doc, plan["source"], now)
        if until is not None:
            out[plan["gid"]] = _deferred(until, now)
        else:
            ready.append(plan)
    tencent = [p for p in ready if p["source"] == "tencent"]
    other = [p for p in ready if p["source"] != "tencent"]

    def seq(batch: list[dict]) -> dict:
        found = {}
        frozen = None
        for plan in batch:
            if frozen is not None:
                found[plan["gid"]] = _deferred(frozen.retry_at, now)
                continue
            result = _one(plan, fetch, now)
            found[plan["gid"]] = result
            err = result.get("error")
            if isinstance(err, sources.RateLimited):
                frozen = err
        return found

    if not ready:
        return out
    if tencent and other:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(seq, other)
            out.update(seq(tencent))
            out.update(fut.result())
        return out
    out.update(seq(tencent or other))
    return out


def _apply(doc: dict, plan: dict, result: dict, now: datetime) -> str:
    """Merge one due group. Returns fetched, unchanged, or failed."""
    meta = plan["meta"]
    gid = plan["gid"]
    iso = now.isoformat(timespec="seconds")
    meta["duration_ms"] = result["duration_ms"]
    http = result.get("http") or {}
    if http.get("attempts"):
        meta["http_attempts"] = http["attempts"]
        meta["http_wait_s"] = http["wait_s"]
    else:
        meta.pop("http_attempts", None)
        meta.pop("http_wait_s", None)
    err = result["error"]
    got = result["got"] or {}
    if isinstance(err, sources.RateLimited):
        eligible = _set_cooldown(doc, plan["source"], err.retry_at)
        meta["status"] = "rate_limited"
        meta["next_eligible_at"] = eligible
        meta["error"] = f"RateLimited: next eligible {eligible}"[:160]
        meta["error_at"] = iso
        meta.pop("quote_time_origin", None)
        if result.get("deferred"):
            meta.pop("duration_ms", None)
            meta.pop("http_attempts", None)
            meta.pop("http_wait_s", None)
        return "rate_limited"
    if err is not None or not got:
        meta["status"] = "failed"
        meta["error"] = (f"{type(err).__name__}: {str(err)[:160]}" if err is not None else "empty quote response")
        meta["error_at"] = iso
        meta.pop("quote_time_origin", None)
        meta.pop("next_eligible_at", None)
        return "failed"
    changed = False
    missing = []
    fresh = {}
    for s in plan["symbols"]:
        q = got.get(s["code"])
        if not q:
            missing.append(s["sym"])
            continue
        prev = (doc.get("quotes") or {}).get(s["key"])
        if prev is None or prev.get("price") != q.get("price"):
            changed = True
        fresh[s["key"]] = {
            "sym": s["sym"],
            "group": gid,
            "market": plan["market"],
            "source": plan["source"],
            "fetched_at": iso,
            **{k: v for k, v in q.items() if k != "name"},
        }
    if not fresh:
        meta["status"] = "failed"
        meta["error"] = "empty quote response"
        meta["error_at"] = iso
        meta["missing"] = missing
        meta.pop("next_eligible_at", None)
        return "failed"
    for key, quote in fresh.items():
        doc["quotes"][key] = quote
    meta["fetched_at"] = iso
    meta["missing"] = missing
    meta.pop("error", None)
    meta.pop("error_at", None)
    meta.pop("next_eligible_at", None)
    origin = http.get("quote_time_origin")
    if not origin:
        if plan["source"] == "tencent":
            origin = "source" if any(q.get("quote_time") for q in fresh.values()) else "unavailable"
        else:
            origin = "exchange" if all(q.get("quote_time") for q in fresh.values()) else "unavailable"
    meta["quote_time_origin"] = origin
    if http.get("origin_error"):
        meta["origin_error"] = http["origin_error"]
    else:
        meta.pop("origin_error", None)
    # A partial book is not "unchanged": the missing symbols are visible as a real gap.
    status = "fetched" if changed or missing else "unchanged"
    meta["status"] = status
    return status


def _summary(rows: list[tuple[str, str, int | None]]) -> str:
    parts = []
    for gid, status, dur in rows:
        if dur is None:
            parts.append(f"{gid}={status}")
        else:
            parts.append(f"{gid}={status}/{dur}ms")
    return " ".join(parts)


def run(now: datetime | None = None, *, force: set | None = None, prev: dict | None = None, fetch=None) -> tuple[dict, list[str], dict]:
    """Return (document, successfully observed group ids, cycle report).

    The report is this cycle, including not-due and closed groups. The document
    is only stamped with those statuses when a group was due or the session
    flags changed, so a pure no-op tick does not rewrite the file.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(BJ)
    cfg = symbols()
    base = prev if prev is not None else load()
    doc = json.loads(json.dumps(base))
    doc.setdefault("quotes", {})
    groups = doc.setdefault("groups", {})
    _expire_cooldowns(doc, now)
    fetch = fetch or _fetch
    plans = []
    for gid, g in cfg["groups"].items():
        market = g.get("market") or gid
        meta = groups.setdefault(gid, {})
        meta.update({"market": market, "source": g["source"], **_market_state(market, now)})
        last = meta.get("fetched_at")
        last_dt = datetime.fromisoformat(last) if last else None
        due = bool(force and (gid in force or "all" in force)) or mc.should_fetch(market, now, last_dt)
        plans.append({
            "gid": gid,
            "market": market,
            "source": g["source"],
            "codes": [s["code"] for s in g["symbols"]],
            "symbols": g["symbols"],
            "meta": meta,
            "due": due,
            "open": meta.get("open"),
        })
    due = [p for p in plans if p["due"]]
    results = _fetch_due(due, fetch, now, doc) if due else {}
    outcomes = {}
    done = []
    for plan in plans:
        if not plan["due"]:
            outcomes[plan["gid"]] = ("closed" if not plan["open"] else "not_due", None)
            continue
        status = _apply(doc, plan, results[plan["gid"]], now)
        outcomes[plan["gid"]] = (status, plan["meta"].get("duration_ms"))
        if status in ("fetched", "unchanged"):
            done.append(plan["gid"])
    material = bool(due) or _market_view(doc) != _market_view(base) or (doc.get("cooldowns") or {}) != (base.get("cooldowns") or {})
    if material:
        for plan in plans:
            status, _dur = outcomes[plan["gid"]]
            plan["meta"]["status"] = status
            plan["meta"]["source_lag_s"] = freshness.source_lag_s(doc.get("quotes") or {}, plan["gid"], plan["meta"].get("fetched_at"))
            plan["meta"]["health"] = freshness.judge(plan["gid"], plan["meta"], now)
        if done:
            doc["generated_at"] = now.isoformat(timespec="seconds")
        rows = [(p["gid"], outcomes[p["gid"]][0], outcomes[p["gid"]][1]) for p in plans]
        doc["cycle"] = {"ran_at": now.isoformat(timespec="seconds"), "role": "collector", "summary": _summary(rows)}
    # Session fields were refreshed on `doc` even when nothing is due. If they did
    # not change and nothing was due, drop them by returning a copy that matches
    # `base` plus any identical rewrite — callers compare via write().
    if not material:
        doc = json.loads(json.dumps(base))
    report = _report(doc if material else base, plans, outcomes, now)
    return doc, done, report


def _report(doc: dict, plans: list[dict], outcomes: dict, now: datetime) -> dict:
    groups = {}
    rows = []
    stored = doc.get("groups") or {}
    for plan in plans:
        gid = plan["gid"]
        status, dur = outcomes.get(gid, ("not_due", None))
        meta = stored.get(gid) or plan["meta"]
        # Health follows the stored facts (previous quotes on failure), not generated_at.
        health_meta = dict(meta)
        health_meta["open"] = plan["open"]
        if status == "failed":
            health_meta["error"] = (plan["meta"].get("error") if plan["due"] else meta.get("error")) or "failed"
        groups[gid] = {
            "due": bool(plan["due"]),
            "status": status,
            "health": freshness.judge(gid, health_meta, now),
            "duration_ms": dur,
            "as_of": meta.get("fetched_at"),
            "open": plan["open"],
            "source_lag_s": freshness.source_lag_s(doc.get("quotes") or {}, gid, meta.get("fetched_at")),
            "quote_time_origin": plan["meta"].get("quote_time_origin") or meta.get("quote_time_origin"),
            "error": plan["meta"].get("error") if status in ("failed", "rate_limited") else None,
            "origin_error": plan["meta"].get("origin_error") if plan["due"] else meta.get("origin_error"),
            "http_attempts": plan["meta"].get("http_attempts") if plan["due"] else meta.get("http_attempts"),
            "http_wait_s": plan["meta"].get("http_wait_s") if plan["due"] else meta.get("http_wait_s"),
            "next_eligible_at": meta.get("next_eligible_at"),
        }
        rows.append((gid, status, dur))
    return {
        "role": "collector",
        "ran_at": now.isoformat(timespec="seconds"),
        "summary": _summary(rows),
        "generated_at": doc.get("generated_at"),
        "groups": groups,
    }


def _fetch(source: str, codes: list[str], now: datetime) -> dict:
    if source == "tencent":
        return sources.tencent(codes)
    if source == "coinbase":
        return sources.coinbase(codes, now=now)
    raise ValueError(source)


def status_view(doc: dict, now: datetime | None = None) -> dict:
    """Small machine-readable snapshot. No per-asset quotes."""
    now = (now or datetime.now(timezone.utc)).astimezone(BJ)
    groups = {}
    for gid, meta in (doc.get("groups") or {}).items():
        groups[gid] = {
            "status": meta.get("status"),
            "health": freshness.judge(gid, meta, now),
            "fetched_at": meta.get("fetched_at"),
            "open": meta.get("open"),
            "market": meta.get("market"),
            "source": meta.get("source"),
            "duration_ms": meta.get("duration_ms"),
            "http_attempts": meta.get("http_attempts"),
            "http_wait_s": meta.get("http_wait_s"),
            "source_lag_s": meta.get("source_lag_s"),
            "quote_time_origin": meta.get("quote_time_origin"),
            "missing": meta.get("missing") or [],
            "error": meta.get("error"),
            "error_at": meta.get("error_at"),
            "origin_error": meta.get("origin_error"),
            "next_eligible_at": meta.get("next_eligible_at"),
        }
    cycle = doc.get("cycle") or {}
    return {
        "role": "collector",
        "ran_at": cycle.get("ran_at"),
        "summary": cycle.get("summary"),
        "generated_at": doc.get("generated_at"),
        "note": "generated_at is the last cycle that observed any group. health and fetched_at are per group. An unchanged price with a new fetched_at is a successful observation. closed is not a failure.",
        "groups": groups,
    }


def _dump(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write(doc: dict, path: Path = OUT) -> bool:
    """Atomically replace the file when content changed. True when it changed."""
    text = _dump(doc)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    _atomic(path, text)
    return True


def write_status(doc: dict, path: Path = STATUS, now: datetime | None = None) -> bool:
    if now is None and (doc.get("cycle") or {}).get("ran_at"):
        now = datetime.fromisoformat(doc["cycle"]["ran_at"])
    return write(status_view(doc, now), path)


HISTORY = ROOT / "data" / "history" / "crypto"
HIST_KEYS = ("btc", "eth")


def append_history(doc: dict, base: Path = HISTORY) -> Path | None:
    """Append one compact row (Beijing time, BTC, ETH in USD) to data/history/crypto/YYYY-MM.csv.

    Append-only; one file per month (~4.4k rows ≈ 150 KB). Skips if the row's timestamp is already the last line.
    A flat price is still appended: unchanged is an observation, not a failed fetch.
    """
    meta = (doc.get("groups") or {}).get("crypto") or {}
    ts = meta.get("fetched_at")
    q = doc.get("quotes") or {}
    if not ts or not all(k in q and q[k].get("fetched_at") == ts for k in HIST_KEYS):
        return None
    path = base / f"{ts[:7]}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = ",".join([ts[:16]] + [f'{q[k]["price"]:.2f}' for k in HIST_KEYS]) + "\n"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[-1].split(",")[0] == ts[:16]:
            return None
    else:
        path.write_text("time_bj," + ",".join(f"{k}_usd" for k in HIST_KEYS) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(row)
    return path
