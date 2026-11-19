# personal-news-data

Public market data and the session-aware fetch job behind the live prices on
[personal-news-site](https://personal-news-site.vercel.app) (全球宏观 · 资产行情).

The site reads `data/live/prices.json` from this repo through a same-origin Vercel rewrite
(`/live/prices.json` → `raw.githubusercontent.com/justinchou/personal-news-data/main/data/live/prices.json`),
so data updates here never trigger a site redeploy. BTC/ETH are fetched here every 10 minutes, 24/7, and appended to
`data/history/crypto/YYYY-MM.csv`; the browser also reads Coinbase Exchange every 60 s while the page is visible and
falls back to the latest value in `prices.json` if that fails.

The Actions run that lasts ~5h40 is the sleep loop, not one request. A skipped run named `watchdog` is the healthy
check that a `collector` is already running. Per-group freshness is in `data/live/status.json` and each group's
`fetched_at` — not in the job duration, and not in `generated_at` alone. Measurements, tradeoffs, and how to hand
over without overlapping writers: [docs/runtime.md](docs/runtime.md).

## Layout

```
.github/workflows/live-prices.yml   self-renewing runner loop (fetch every 5 min) + watchdog cron; see Scheduling
.github/workflows/tests.yml         unit tests
config/market_calendar.yml          sessions, holidays, early closes per market (official sources cited inline)
config/symbols.yml                  symbols per group, source labels and delay notes (generated — see docs/watchlist.md)
docs/watchlist.md                   maintaining symbols and the yearly holiday tables (Chinese)
docs/runtime.md                     measured timing, why the long loop stays, hand-over steps
livedata/market_calendar.py         status(market, now), should_fetch(...) — tiered, session-aware schedule
livedata/freshness.py               health from fetched_at / error / open; ignores generated_at
livedata/sources.py                 Tencent batches (serial) and Coinbase ticker+stats; bounded backoff
livedata/live.py                    merge into data/live/prices.json; one Coinbase sequence may overlap Tencent
scripts/fetch_live.py               CLI: one cycle, --force all|group,..., --status
scripts/live_loop.sh                the 5-minute loop inside one Actions job; commits data/ only when it changed
scripts/loop_guard.py               watchdog: start a collector only when none is active, and print stored freshness
tests/                              calendar, parser, gating, unchanged-vs-failed, freshness, workflow contract
data/live/prices.json               latest quotes (Asia/Shanghai timestamps)
data/live/status.json               last material cycle, per group, no per-asset quotes
data/history/crypto/YYYY-MM.csv     append-only BTC/ETH every 10 min: time_bj,btc_usd,eth_usd (~150 KB/month)
```

## Schedule

| Group | Session calendar | While open | After close | Source |
|---|---|---|---|---|
| us_equity (watchlist US stocks + TLT) | NYSE 09:30–16:00 ET, holidays/early closes | 10 min | once, +20 min | Tencent |
| hk_equity | HKEX 09:30–16:10 HKT, half days | 10 min | once, +20 min | Tencent |
| cn_equity | SSE/SZSE 09:30–11:30, 13:00–15:00 | 10 min | once, +15 min | Tencent |
| futures_fx (WTI, Brent, COMEX gold, USD/CNY) | CME Globex Sun 18:00 – Fri 17:00 ET, daily break 17:00–18:00 | 15 min | once, +30 min | Tencent |
| crypto (BTC, ETH) | 24/7 | 10 min + history (browser: 60 s) | — | Coinbase Exchange |

A fetch is accepted up to 2 minutes early to absorb start-time jitter.

## Scheduling (GitHub only)

A 5-minute `schedule:` cron alone did not work: GitHub drops or delays scheduled runs under load
([docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)).
On this repo the watchdog cron `11,41 * * * *` was delivered 6 times in the ~47 h after the collector chain started,
not every 30 min. The cadence is a collector process that sleeps on the runner and wakes itself:

1. **Collector** (`run-name: collector`) — `scripts/live_loop.sh` every 5 minutes (at :02, :07, …) pulls `main`,
   runs `scripts/fetch_live.py`, and commits `data/` only when it changed. The log line's `summary` is the cycle
   (`fetched`, `unchanged`, `failed`, `rate_limited`, `closed`, `not_due`) with that cycle's duration. Most ticks fetch nothing.
   An origin failure is still committed: the previous quote stays, `status=failed`, and `generated_at` does not move
   unless some other group succeeded.
2. **Hand-over** — after ~5 h 40 min (the job limit is 6 h) the job dispatches the next run on `main` and exits.
   Concurrency group `live-prices-loop` with `cancel-in-progress: false` holds the new run until this job finishes.
   Do not rename that group while a collector is in flight: the queued run would no longer wait for the old one.
3. **Watchdog** (`run-name: watchdog`) — cron, or a manual run with `mode=watchdog`. It prints whether a collector
   is active and the freshness already stored in `prices.json`, then exits. The `collector` job on that run is
   **skipped on purpose**. It starts a collector only when none is active or queued.

The runner stays up almost all day. That is the reliability tradeoff: one successful hand-off covers ~5 h 40 min of
locally timed ticks, and the cron that would replace it was not delivered on time (see [docs/runtime.md](docs/runtime.md)).
Switching to a pure 15-minute cron is not a reliable substitute. Manual run: Actions → live-prices → Run workflow
(`mode=loop` queues behind the active collector; `mode=watchdog` only checks; `minutes` shortens a test loop).
To stop: disable the workflow. Do not cancel the active collector to "pick up" a code change; the next hand-over
checks out `main`.

## Sources, limits, delay

- **Tencent quotes** (`qt.gtimg.cn`): undocumented public endpoint with no published rate limits or terms.
  One batched request per group. Tencent groups never run at the same time. Coinbase may run beside that chain
  (at most one Coinbase sequence). Connect timeout 3 s, read timeout 10 s, then 2 s and 8 s backoff.
  `Retry-After` (seconds or HTTP-date) is waited in full when it fits in a 30 s in-cycle budget.
  A longer cooldown is stored and that source is skipped until it expires; the other source still runs.
  Previous quotes are kept on failure. Delay is not published —
  each quote carries the source's own quote time (`quote_time`). A fresh `fetched_at` with an old `quote_time`
  is source lag, not a missed fetch. On this feed, HK names have been ~15 min behind the fetch; CN and US have not.
- **Coinbase Exchange** public REST: 10 requests/s per IP, burst 15
  ([docs](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)). Each 10-minute cycle requests the ticker
  and the 24 h stats for BTC and ETH (4 requests). `quote_time` is the ticker's trade time. The fetch clock is
  `fetched_at` and is not copied into `quote_time` when the ticker time is missing. Change shown is vs. the 24 h open.
  The browser still makes its own 2 requests per 60 s.
- Yahoo Finance is deliberately not used here (its help page says the data must not be redistributed,
  [SLN2310](https://help.yahoo.com/kb/SLN2310.html)).

Times in `prices.json` are ISO 8601 with `+08:00` (Beijing time). Not investment advice.

## Local

```
pip install -r requirements.txt
python scripts/fetch_live.py --status
python scripts/fetch_live.py --force all
python -m unittest discover -s tests -t .
```

Code: MIT (see LICENSE). Quote data belongs to its respective sources.
