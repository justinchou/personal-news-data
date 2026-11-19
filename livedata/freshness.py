"""Per-group freshness from stored facts. Stdlib only so the watchdog can import it.

`generated_at` is the last time any group was observed successfully. It does not
make another group's quote fresh. Health is judged from that group's own
`fetched_at`, `error`, and `open` flag.

LIMIT_MIN is REFRESH[market].open + the 2-minute schedule tolerance. tests/test_freshness.py
locks the two together.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Minutes. crypto/equities: 10 + 2. Globex: 15 + 2.
LIMIT_MIN = {
    "us_equity": 12,
    "hk_equity": 12,
    "cn_equity": 12,
    "cme_globex": 17,
    "crypto": 12,
}


def parse_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def judge(group_id: str, meta: dict, now: datetime) -> str:
    """ok, stale, closed, or failed. Closed markets are not failures."""
    if meta.get("error"):
        return "failed"
    if meta.get("open") is False:
        return "closed"
    market = meta.get("market") or group_id
    limit = LIMIT_MIN.get(market)
    if limit is None:
        limit = LIMIT_MIN.get(group_id)
    fetched = parse_time(meta.get("fetched_at"))
    if fetched is None or limit is None:
        return "stale"
    age_min = (now - fetched).total_seconds() / 60
    return "ok" if age_min <= limit else "stale"


def source_lag_s(quotes: dict, group_id: str, fetched_at: str | None) -> int | None:
    """Seconds the newest stored source timestamp lags the group's fetch. None if unknown.

    This is source delay (HK prints on this feed run ~15 min behind), not proof the
    fetch failed. A missing quote_time stays None rather than being copied from the fetch clock.
    """
    anchor = parse_time(fetched_at)
    if anchor is None:
        return None
    lags = []
    for quote in quotes.values():
        if quote.get("group") != group_id:
            continue
        qt = parse_time(quote.get("quote_time"))
        if qt is None:
            continue
        lags.append(int((anchor - qt).total_seconds()))
    return max(lags) if lags else None


def summarize_prices(doc: dict, now: datetime) -> str:
    """One line per group. Safe to print from the watchdog; no network."""
    lines = [
        "generated_at=%s (document time of the last successful group; not per-quote freshness)"
        % doc.get("generated_at")
    ]
    groups = doc.get("groups") or {}
    for gid, meta in groups.items():
        fetched = parse_time(meta.get("fetched_at"))
        age = None if fetched is None else round((now - fetched).total_seconds() / 60, 1)
        eligible = (" next_eligible_at=%s" % meta["next_eligible_at"]) if meta.get("next_eligible_at") else ""
        lines.append(
            "%s health=%s age_min=%s fetched_at=%s status=%s open=%s source_lag_s=%s error=%s%s"
            % (
                gid,
                judge(gid, meta, now),
                age,
                meta.get("fetched_at"),
                meta.get("status"),
                meta.get("open"),
                meta.get("source_lag_s"),
                meta.get("error"),
                eligible,
            )
        )
    return "\n".join(lines)
