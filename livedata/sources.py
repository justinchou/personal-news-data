"""Quote fetchers.

Tencent (qt.gtimg.cn) has no published rate limit, so each market is one batched
request and live.run never runs two Tencent batches at once. Coinbase is a
different host: live.run may run it beside the Tencent chain, bounded to one
Coinbase sequence in flight. Products inside Coinbase stay sequential.

HTTP: connect 3s, read 10s, two retries. Backoff is 2s then 8s. Retry-After is
honored but capped at 15s, so one call stays within about a minute even if the
origin asks for a long pause. No retry was observed in the Nov 17–19 collector
logs; the cap is a bound, not a response to a measured 429.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "personal-news-data/1.0 (+https://github.com/justinchou/personal-news-data)"}
BJ = ZoneInfo("Asia/Shanghai")
TZ = {"us": ZoneInfo("America/New_York"), "hk": ZoneInfo("Asia/Hong_Kong")}
BACKOFF = (2, 8)  # seconds before retry 1 and 2
RETRY_AFTER_CAP = 15
TIMEOUT = (3.05, 10)  # connect, read

_tls = threading.local()


class _Info:
    def __init__(self):
        self.attempts = 0
        self.wait_s = 0.0
        self.requests = 0
        self.quote_time_origin = None
        self.origin_error = None

    def as_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "wait_s": round(self.wait_s, 3),
            "requests": self.requests,
            "quote_time_origin": self.quote_time_origin,
            "origin_error": self.origin_error,
        }


def begin_info() -> None:
    """Start a per-thread tally. Call on the thread that will perform the fetch."""
    _tls.info = _Info()


def current_info() -> _Info:
    info = getattr(_tls, "info", None)
    if info is None:
        info = _Info()
        _tls.info = info
    return info


def snapshot_info() -> dict:
    return current_info().as_dict()


def get(url: str, *, sleep=time.sleep, session=requests, timeout=TIMEOUT) -> requests.Response:
    info = current_info()
    info.requests += 1
    last = None
    for i in range(len(BACKOFF) + 1):
        info.attempts += 1
        try:
            r = session.get(url, headers=UA, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if i == len(BACKOFF):
                break
            wait = BACKOFF[i]
            ra = getattr(getattr(e, "response", None), "headers", {}) or {}
            retry_after = str(ra.get("Retry-After", ""))
            if retry_after.isdigit():
                wait = max(wait, min(int(retry_after), RETRY_AFTER_CAP))
            info.wait_s += wait
            sleep(wait)
    raise last


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _iso_bj(dt: datetime | None):
    return dt.astimezone(BJ).isoformat(timespec="seconds") if dt else None


def parse_exchange_time(value: str | None) -> datetime | None:
    """Coinbase ticker times look like 2026-11-19T01:42:20.875145373Z (nanoseconds)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, frac = text.split(".", 1)
        i = 0
        while i < len(frac) and frac[i].isdigit():
            i += 1
        digits, tail = frac[:i], frac[i:]
        text = head + "." + (digits[:6].ljust(6, "0") if digits else "000000") + tail
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_tencent(text: str) -> dict:
    """code -> {price, prev_close, change_pct, quote_time (ISO, +08:00), name}. Unknown codes are skipped."""
    out = {}
    for line in text.split(";"):
        line = line.strip()
        if "=" not in line or not line.startswith("v_"):
            continue
        k, v = line.split("=", 1)
        code, v = k[2:], v.strip().strip('"')
        if code.startswith("pv_none") or not v:
            continue
        try:
            if code.startswith("hf_"):  # futures: price, pct, ..., time, prev_settle, ..., date, name
                f = v.split(",")
                price, prev = _num(f[0]), _num(f[7])
                qt = datetime.strptime(f"{f[12]} {f[6]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
                name = f[13] if len(f) > 13 else code
            elif code.startswith(("wh", "fx")):  # FX: ~-separated; [3] price, [5] yyyymmddHHMMSS (Beijing), [12] change
                f = v.split("~")
                price, chg = _num(f[3]), _num(f[12])
                prev = None if price is None or chg is None else price - chg
                qt = datetime.strptime(f[5], "%Y%m%d%H%M%S").replace(tzinfo=BJ)
                name = f[1]
            else:  # stocks us/hk/sh/sz: [1] name, [3] price, [4] prev close, [30] exchange-local time
                f = v.split("~")
                price, prev, name = _num(f[3]), _num(f[4]), f[1]
                ts = f[30].strip()
                if code.startswith(("us", "hk")):
                    fmt = "%Y/%m/%d %H:%M:%S" if "/" in ts else "%Y-%m-%d %H:%M:%S"
                    qt = datetime.strptime(ts, fmt).replace(tzinfo=TZ[code[:2]])
                else:
                    qt = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=BJ)
        except (IndexError, ValueError):
            continue
        if price is None or price <= 0:
            continue
        pct = None if not prev else round((price / prev - 1) * 100, 3)
        out[code] = {"price": price, "prev_close": prev, "change_pct": pct, "quote_time": _iso_bj(qt), "name": name}
    return out


def tencent(codes: list[str], **kw) -> dict:
    current_info().quote_time_origin = "source"
    r = get("https://qt.gtimg.cn/q=" + ",".join(codes), **kw)
    r.encoding = "gbk"
    return parse_tencent(r.text)


def coinbase(products: list[str], *, now: datetime, **kw) -> dict:
    """BTC/ETH last trade from the ticker (price + exchange time) and the 24h open from stats.

    `now` is the observation clock used by the caller for fetched_at. It is never
    copied into quote_time: a missing ticker time stays null so an old price cannot
    look freshly timestamped.
    """
    del now
    out = {}
    info = current_info()
    errors = []
    timed = 0
    for p in products:  # sequential; 10 req/s documented, we do 4 requests per 10 min
        stats = get(f"https://api.exchange.coinbase.com/products/{p}/stats", **kw).json()
        ticker = None
        try:
            ticker = get(f"https://api.exchange.coinbase.com/products/{p}/ticker", **kw).json()
        except requests.RequestException as e:
            errors.append(f"{p} ticker {type(e).__name__}")
        ticker_price = _num((ticker or {}).get("price"))
        trade_time = parse_exchange_time((ticker or {}).get("time")) if ticker_price is not None else None
        if ticker_price is None:
            price = _num(stats.get("last"))
            trade_time = None
            if not any(err.startswith(p + " ") for err in errors):
                errors.append(f"{p} ticker missing price")
        else:
            price = ticker_price
            if trade_time is None:
                errors.append(f"{p} ticker missing time")
        if trade_time is not None:
            timed += 1
        op = _num(stats.get("open"))
        if price:
            out[p] = {
                "price": price,
                "prev_close": op,
                "change_pct": None if not op else round((price / op - 1) * 100, 3),
                "quote_time": _iso_bj(trade_time) if trade_time else None,
                "change_basis": "24h",
            }
    info.quote_time_origin = "exchange" if out and timed == len(out) else "unavailable"
    info.origin_error = "; ".join(errors)[:160] if errors else None
    return out
