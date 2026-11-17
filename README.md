# personal-news-data

Public market data and the session-aware fetch job behind the live prices on
[personal-news-site](https://personal-news-site.vercel.app) (全球宏观 · 资产行情).

The site reads `data/live/prices.json` from this repo through a same-origin Vercel rewrite
(`/live/prices.json` → `raw.githubusercontent.com/justinchou/personal-news-data/main/data/live/prices.json`),
so data updates here never trigger a site redeploy. BTC/ETH are fetched here every 10 minutes, 24/7, and appended to
`data/history/crypto/YYYY-MM.csv`; the browser also reads Coinbase Exchange every 60 s while the page is visible and
falls back to the latest value in `prices.json` if that fails.

## Layout

```
.github/workflows/live-prices.yml   self-renewing runner loop (fetch every 5 min) + watchdog cron; see Scheduling
.github/workflows/tests.yml         unit tests
config/market_calendar.yml          sessions, holidays, early closes per market (official sources cited inline)
config/symbols.yml                  symbols per group, source labels and delay notes (generated — see docs/watchlist.md)
docs/watchlist.md                   maintaining symbols and the yearly holiday tables (Chinese)
livedata/market_calendar.py         status(market, now), should_fetch(...) — tiered, session-aware schedule
livedata/sources.py                 Tencent batched quotes, Coinbase 24h stats; sequential, retry with backoff
livedata/live.py                    merge into data/live/prices.json, keep previous quotes on failure
scripts/fetch_live.py               CLI: one run, --force all|group,..., --status
scripts/live_loop.sh                the 5-minute loop inside one Actions job; commits data/ only when it changed
scripts/loop_guard.py               watchdog check: is a loop run already active?
tests/                              calendar, parser, gating, failure handling
data/live/prices.json               latest quotes (Asia/Shanghai timestamps)
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
([docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)),
and on this new repo it fired once in ~13 hours. So the cadence comes from a runner loop instead:

1. **Loop run** — the `loop` job of `live-prices.yml` runs `scripts/live_loop.sh`: every 5 minutes (at :02, :07, …)
   it pulls `main`, runs `scripts/fetch_live.py` (the market calendar decides what is due; most iterations are a
   no-op) and commits/pushes `data/` only when it changed (`git pull --rebase` before every push).
   A failed iteration is logged and the loop carries on.
2. **Hand-over** — after ~5 h 40 min (GitHub's job limit is 6 h) the job dispatches the next run of the workflow with
   `GITHUB_TOKEN` (`actions: write`; `workflow_dispatch` is one of the events `GITHUB_TOKEN` may trigger) and exits.
   The job-level concurrency group `live-prices-loop` holds the new run until the old one finishes, so two loops never
   run at the same time (at most one is running and one is waiting).
3. **Watchdog** — the cron `11,41 * * * *` only checks: `scripts/loop_guard.py` lists this workflow's runs and the
   watchdog exits in seconds if a loop run is active or queued; otherwise (loop failed, cancelled or never handed
   over) it starts the loop itself. Even if GitHub skips some watchdog runs, one getting through is enough.

A runner is therefore busy almost continuously (~24 h/day); standard runners are free for public repositories.
Expect a gap of about a minute at each hand-over, and up to ~30 min (more if GitHub skips watchdog runs) after a
loop dies. Manual run: Actions → live-prices → Run workflow (`mode=loop` starts a loop, or queues one behind the active
loop; `mode=watchdog` does the watchdog check; `minutes` shortens a test loop). To stop: disable the workflow.

## Sources, limits, delay

- **Tencent quotes** (`qt.gtimg.cn`): undocumented public endpoint with no published rate limits or terms.
  Used lightly: one batched request per group per slot, sequential, retries after 5 s / 20 s (honours `Retry-After`),
  previous quotes kept on failure. Delay is not published — each quote carries the source's own quote time.
- **Coinbase Exchange** public REST: 10 requests/s per IP, burst 15
  ([docs](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)). The job makes 2 sequential requests per
  10 minutes; each browser makes 2 per 60 s. Change shown is vs. the 24 h open.
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
