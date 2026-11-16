"""Quote fetchers. One sequential request per group, retries with backoff, never parallel.

- Tencent quotes (qt.gtimg.cn): undocumented public endpoint, no published limits/terms → one batched request per
  market per scheduled slot (>= 10 min apart), back off on errors / HTTP 429.
- Coinbase Exchange public REST (api.exchange.coinbase.com): documented 10 req/s per IP, burst 15
  (https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits). We make 2 requests per fallback run.
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "personal-news-data/1.0 (+https://github.com/justinchou/personal-news-data)"}
BJ = ZoneInfo("Asia/Shanghai")
TZ = {"us": ZoneInfo("America/New_York"), "hk": ZoneInfo("Asia/Hong_Kong")}
BACKOFF = (5, 20)  # seconds before retry 1 and 2


def get(url: str, *, sleep=time.sleep, session=requests) -> requests.Response:
    last = None
    for i in range(len(BACKOFF) + 1):
        try:
            r = session.get(url, headers=UA, timeout=20)
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
            if ra.get("Retry-After", "").isdigit():
                wait = max(wait, min(int(ra["Retry-After"]), 120))
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
    r = get("https://qt.gtimg.cn/q=" + ",".join(codes), **kw)
    r.encoding = "gbk"
    return parse_tencent(r.text)


def coinbase(products: list[str], *, now: datetime, **kw) -> dict:
    out = {}
    for p in products:  # sequential; 24h stats gives last + open
        d = get(f"https://api.exchange.coinbase.com/products/{p}/stats", **kw).json()
        last, op = _num(d.get("last")), _num(d.get("open"))
        if last:
            out[p] = {"price": last, "prev_close": op, "change_pct": None if not op else round((last / op - 1) * 100, 3),
                      "quote_time": _iso_bj(now), "change_basis": "24h"}
    return out
