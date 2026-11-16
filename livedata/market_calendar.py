"""Session-aware market calendar for the live price job.

status(market, now) → open/closed, current session, next open/close (exchange tz + Asia/Shanghai), and whether
the holiday table covers the date. Data: config/market_calendar.yml (official sources cited there).
Used by livedata/live.py to decide which markets to fetch on each scheduled run.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import yaml

from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

BJ = ZoneInfo("Asia/Shanghai")
DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Refresh plan (minutes) per market state; "after_close" = one fetch this long after the close.
# Crypto: the job fetches BTC/ETH every 10 min, 24/7, and appends them to data/history/crypto/ (the browser
# additionally reads Coinbase every 60 s while the page is visible and falls back to the file).
REFRESH = {
    "us_equity": {"open": 10, "after_close": 20},
    "hk_equity": {"open": 10, "after_close": 20},
    "cn_equity": {"open": 10, "after_close": 15},
    "cme_globex": {"open": 15, "after_close": 30},
    "crypto": {"open": 10},
}
# Scheduled runs fire every 5 minutes but GitHub may start them late; accept a fetch this much early.
TOLERANCE = timedelta(minutes=2)


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "market_calendar.yml").read_text(encoding="utf-8"))["markets"]


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _sessions(m: dict, d: date) -> list[tuple[datetime, datetime]]:
    """Trading intervals for local date d (empty if closed)."""
    tz = ZoneInfo(m["tz"])
    if m.get("always_open"):
        start = datetime.combine(d, time(0), tz)
        return [(start, start + timedelta(days=1))]
    if "weekly" in m:  # futures: open Sun 18:00 → Fri 17:00 with a daily break
        w = m["weekly"]
        bs, be = _t(w["daily_break"][0]), _t(w["daily_break"][1])
        wd = d.weekday()
        out = []
        if wd in (6, 0, 1, 2, 3):  # evening session starts on Sun–Thu at break end
            out.append((datetime.combine(d, be, tz), datetime.combine(d + timedelta(days=1), time(0), tz)))
        if wd in (0, 1, 2, 3, 4):  # Mon–Fri: midnight → break start
            out.insert(0, (datetime.combine(d, time(0), tz), datetime.combine(d, bs, tz)))
        hol = {_d(x) for x in m.get("holidays") or []}
        return [] if d in hol else out
    if d.weekday() >= 5 or d in {_d(x) for x in m.get("holidays") or []}:
        return []
    spans = m["sessions"]
    if d in {_d(x) for x in m.get("half_days") or []}:
        spans = m.get("half_day_sessions") or spans
    out = [(datetime.combine(d, _t(a), tz), datetime.combine(d, _t(b), tz)) for a, b in spans]
    ec = (m.get("early_close") or {}).get(d.isoformat())
    if ec:
        out = [(a, min(b, datetime.combine(d, _t(ec), tz))) for a, b in out if a < datetime.combine(d, _t(ec), tz)]
    return out


def calendar_valid(m: dict, d: date) -> bool:
    if m.get("always_open"):
        return True
    vt = m.get("valid_through")
    return bool(vt) and d <= _d(vt)


def status(market: str, now: datetime | None = None) -> dict:
    m = config()[market]
    tz = ZoneInfo(m["tz"])
    now = (now or datetime.now(BJ)).astimezone(tz)
    today = now.date()
    cur = None
    for d in (today - timedelta(days=1), today):
        for a, b in _sessions(m, d):
            if a <= now < b:
                cur = (a, b)
    nxt_open = nxt_close = None
    if cur:
        nxt_close = cur[1]
        # merge contiguous spans (e.g. crypto midnight, futures midnight)
        for i in range(1, 9):
            for a, b in _sessions(m, today + timedelta(days=i - 1)):
                if a <= nxt_close < b or a == nxt_close:
                    nxt_close = b
    for i in range(0, 15):
        for a, b in _sessions(m, today + timedelta(days=i)):
            if a > now and (not cur or a > nxt_close):
                nxt_open = a
                break
        if nxt_open:
            break
    last_close = None
    if not cur:
        for i in range(0, 15):
            spans = [b for a, b in _sessions(m, today - timedelta(days=i)) if b <= now]
            if spans:
                last_close = max(spans)
                break
    f = lambda x: None if x is None else {"local": x.isoformat(timespec="minutes"), "bj": x.astimezone(BJ).strftime("%Y-%m-%d %H:%M")}
    return {"market": market, "name": m["name"], "open": bool(cur), "now_local": now.isoformat(timespec="minutes"),
            "next_close": f(nxt_close) if cur and not m.get("always_open") else None, "next_open": f(nxt_open) if not cur else None,
            "last_close": f(last_close), "calendar_valid": calendar_valid(m, today), "source": m.get("source")}


def should_fetch(market: str, now: datetime, last_fetch: datetime | None, tolerance: timedelta = TOLERANCE) -> bool:
    """Tiered refresh: every REFRESH[open] min while open; once REFRESH[after_close] min after the close; else no."""
    st = status(market, now)
    plan = REFRESH.get(market) or {"open": 15}
    if st["open"]:
        return last_fetch is None or (now - last_fetch) >= timedelta(minutes=plan["open"]) - tolerance
    lc = st["last_close"]
    if not lc or "after_close" not in plan:
        return False
    due = datetime.fromisoformat(lc["local"]) + timedelta(minutes=plan["after_close"])
    return now >= due and (last_fetch is None or last_fetch < due)


def snapshot(now: datetime | None = None) -> list[dict]:
    return [status(k, now) for k in config()]
