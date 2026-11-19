# Collector runtime

What the Actions page shows, what was measured on the public repo, what this change
does, and how to let it take over without two writers or a hole in the crypto history.

Times below are Asia/Shanghai (UTC+8). Run links are the public Actions pages.
"Measured" means it came from the Actions API, a job log, or `git` history of
`data/`. "One-shot" means a single request from the investigation environment, not
from the GitHub runner. Nothing here is a before/after of this patch on the runner:
the patch is not deployed.

## What looks slow, and what is actually slow

From 2026-11-17 09:42 through the snapshot at about 09:40 on 2026-11-19 the
collector is one job that sleeps until the next `:02`/`:07`/`:12` slot. The job
length is ~5 h 40 min because `LOOP_MINUTES` is 340 and GitHub's job limit is 6 h.
The requests are the few seconds at the start of each 5-minute slot.

Measured on [run 44357878530](https://github.com/justinchou/personal-news-data/actions/runs/44357878530)
(job [134990931584](https://github.com/justinchou/personal-news-data/actions/runs/44357878530/job/134990931584),
01:22–07:02 on 2026-11-19, 69 iterations):

| | |
|---|---|
| Work, group start → fetch JSON (includes `git pull`) | min 0.28 s, p50 1.45 s, p90 2.51 s, max 3.02 s |
| Push after that JSON, when there was a commit | p50 1.33 s, max 1.55 s (43 pushes) |
| Gap between ticks | 67 of 68 gaps were 300 s; one was 277 s |
| Iterations with nothing due | 26 / 69 |
| Slowest cycle | US + futures + crypto, 3.02 s |

The same shape on [run 44150667056](https://github.com/justinchou/personal-news-data/actions/runs/44150667056)
(02:42–08:22 on 2026-11-18): work p50 0.44 s, max 2.76 s, 25/69 empty.

The 277 s gap is the first iteration of a job. It runs immediately, and if the next
slot is under 60 s away the loop skips to the one after. It is not a dropped 10-minute
fetch. No `::warning::` came from a fetch in either log. The `::error::` text in those
logs is the shell script being echoed, not a failed step: the hand-over step succeeded.

Recent data commits land in the same Beijing minute as the message, a few seconds
after the slot (example: `09:32` committed at 09:32:03). Commit freshness and the
5-hour job duration are different clocks.

Per-request milliseconds were **not** in those logs. The new cycle record stores
`duration_ms`, `http_attempts`, and `http_wait_s` per group so the next incident is
visible. One-shot timings from this environment at ~09:42 on 2026-11-19, single try,
not the runner:

| Call | Result |
|---|---|
| `GET /products/BTC-USD/stats` | 200 in 72 ms; `last` and `open`; no timestamp |
| `GET /products/BTC-USD/ticker` | 200 in 41 ms; `time` `2026-11-19T01:42:20.875145373Z`; price differed from `stats.last` by a few tens of dollars |
| `GET qt.gtimg.cn/q=sh600900` | 200 in 727 ms |

## Which skips are normal

After the long chain started, these schedule runs completed in well under a minute
and their `loop` job was **skipped** (jobs API):

| Run | Beijing time | Loop job |
|---|---|---|
| [44058318756](https://github.com/justinchou/personal-news-data/actions/runs/44058318756) | 2026-11-17 17:44 | skipped |
| [44155737703](https://github.com/justinchou/personal-news-data/actions/runs/44155737703) | 2026-11-18 03:10 | skipped |
| [44217341460](https://github.com/justinchou/personal-news-data/actions/runs/44217341460) | 2026-11-18 09:38 | skipped |
| [44273694524](https://github.com/justinchou/personal-news-data/actions/runs/44273694524) | 2026-11-18 17:12 | skipped |
| [44369394921](https://github.com/justinchou/personal-news-data/actions/runs/44369394921) | 2026-11-19 02:24 | skipped |
| [44431463794](https://github.com/justinchou/personal-news-data/actions/runs/44431463794) | 2026-11-19 08:44 | skipped |

The 08:44 log says why: collector [44417107050](https://github.com/justinchou/personal-news-data/actions/runs/44417107050)
was `in_progress`, so the watchdog exited. The other five are the same job shape
(guard success, loop skipped) during a chain that had no gap. The reason string was
read for 08:44 only; the rest are that shape plus the continuous chain, not six
downloaded logs.

Those six deliveries are the entire cron output from 09:42 on Nov 17 to 08:44 on
Nov 19 (~47 h). A `11,41 * * * *` cron would have been ~94 runs. Gaps between the
six were 9.4 h, 6.5 h, 7.6 h, 9.2 h, and 6.3 h. A pure 15-minute cron is not something
this repo can advertise as reliable.

The collector chain itself, each ~5 h 40 min, hand-off to the next, all success:

| Run | Beijing window |
|---|---|
| [43997565428](https://github.com/justinchou/personal-news-data/actions/runs/43997565428) | 11-17 09:42 → 15:22 |
| [44038145795](https://github.com/justinchou/personal-news-data/actions/runs/44038145795) | 11-17 15:22 → 21:02 |
| [44088719532](https://github.com/justinchou/personal-news-data/actions/runs/44088719532) | 11-17 21:02 → 11-18 02:42 |
| [44150667056](https://github.com/justinchou/personal-news-data/actions/runs/44150667056) | 11-18 02:42 → 08:22 |
| [44207227311](https://github.com/justinchou/personal-news-data/actions/runs/44207227311) | 11-18 08:22 → 14:02 |
| [44248372546](https://github.com/justinchou/personal-news-data/actions/runs/44248372546) | 11-18 14:02 → 19:42 |
| [44297054646](https://github.com/justinchou/personal-news-data/actions/runs/44297054646) | 11-18 19:42 → 11-19 01:22 |
| [44357878530](https://github.com/justinchou/personal-news-data/actions/runs/44357878530) | 11-19 01:22 → 07:02 |
| [44417107050](https://github.com/justinchou/personal-news-data/actions/runs/44417107050) | 11-19 07:02 → still running at investigation |

Example hand-over: the 07:02 step on Nov 19 dispatched at 07:02:03–07:02:04, and the
next run was created in that same second. Its fetch step started ~15 s later (runner
setup). The listing of all 25 runs at ~09:40 on Nov 19 had one `in_progress` and no
queued second collector. No pile-up was observed. A second dispatch would wait on
`live-prices-loop` (`cancel-in-progress: false`), not write beside the first.

While that run stayed `in_progress`, GitHub left its `updated_at` at 07:02:15. The
run object does not move on each cycle. `origin/main` did: data commits at 09:42,
09:47, and 09:52 on Nov 19 were on the repo during this investigation. A local
checkout from 09:32 therefore looks 20 minutes stale even though the collector is
not. Read `fetched_at` on `main`, not the run's `updated_at`.

Earlier, and not the steady state: [43860650611](https://github.com/justinchou/personal-news-data/actions/runs/43860650611)
failed at 20:15 on Nov 16 in the old one-shot job because a rebase left unmerged files.
[43996142827](https://github.com/justinchou/personal-news-data/actions/runs/43996142827)
was cancelled at 09:31 on Nov 17 during `setup-python`, before this chain. The loop
script aborts a failed rebase before the retry.

## Freshness that was actually achieved

`git` history of `data/live/prices.json` (388 commits, 2026-11-16 19:26 through
2026-11-19 09:32). No commit stored a group `error`.

Crypto history `data/history/crypto/2026-11.csv`: 294 rows, median gap 10.0 min.
Four gaps over 13 min, all before the chain existed:

| From | To | Gap |
|---|---|---|
| 11-16 19:30 | 11-16 19:53 | 23 min |
| 11-16 19:53 | 11-17 05:16 | 563 min |
| 11-17 05:16 | 11-17 08:16 | 180 min |
| 11-17 08:25 | 11-17 09:31 | 66 min |

[43958291792](https://github.com/justinchou/personal-news-data/actions/runs/43958291792)
at 05:16 on Nov 17 was still the old one-shot `fetch` job, which matches that row.
After 09:31 on Nov 17, every crypto interval is ~10 min.

In-session fetch gaps over 13 min (both ends of the gap inside the session): A-shares
0/44, HK 0/78, US 0/76. Futures median gap 15.0 min. The 45 min then 30 min gaps at
05:47 → 06:32 → 07:02 (Nov 18 and Nov 19) are the Globex 17:00–18:00 ET break
(06:00–07:00 Beijing in November) plus the single +30 min post-break fetch, then the
re-open. That is the calendar, not a stuck request.

Source lag at the moment we stored the quote (`fetched_at` minus `quote_time`):

| | |
|---|---|
| A-shares while open | p50 0 s, max 3 s (73 samples) |
| US while the cash session is open | p50 20 s, max 22 s (110) |
| HK while open, after 09:40 | p50 906 s (~15 min), 110 samples. The print advances with our 10 min fetches; the feed itself is behind. The long tail (p90 ~42 min) is when that print stops moving (lunch and the open auction), not when the fetch stops. |
| COMEX gold, all samples | p50 56 s |
| BTC | p50 0 s **by construction**. The old code set `quote_time` to the fetch clock. That is not an exchange timestamp. |

US `fetched_at` 05:22 on Nov 19 against a 05:00 quote is the +20 min post-close fetch
(cash session ends 05:00 Beijing in November). Closed, not a failure.

`generated_at` moved whenever any group succeeded. A failed group would have kept its
old quote under a new document time. No such failure was stored in these 388 commits;
the code still could not say so. A flat price rewrote `fetched_at` and committed, which
is a successful observation, but the file had no status that distinguished it from a miss.

## What changed, and what did not

Polling intervals are unchanged: US/HK/A 10 min while open, futures 15 min, crypto
10 min 24/7, one post-close fetch, 2 min early tolerance, 5 min tick. History CSV
columns are unchanged. `schema` stays 1. New fields are additive.

Code changes:

- Cycle vocabulary on each group and in the log JSON: `due` (boolean) and `status` of `fetched`, `unchanged`, `failed`, `closed`, or `not_due`. `unchanged` still advances `fetched_at` and still appends the crypto history row. `failed` and an empty payload keep the previous quote and do not move that group's `fetched_at`. `generated_at` moves only when some group was observed. `cycle.ran_at` still moves on a total failure, so a touched document time is not a fresh quote.
- Coinbase `quote_time` comes from the ticker trade time. Stats still supply the 24 h open. If the ticker fails, the stats price is kept, `quote_time` is null, and `origin_error` is set. The fetch clock is never written into `quote_time`.
- Health (`ok` / `stale` / `closed` / `failed`) is computed from that group's `fetched_at`, `error`, and `open` flag. Limits are the open interval plus 2 min (12 or 17). A closed session is not `stale`. Source lag is reported separately and does not fail the group.
- Tencent groups stay one batch at a time. The Coinbase sequence may run beside that chain (one extra thread, so two sequences in flight). Happy-path cycles were already under 3 s; the overlap is so a Tencent timeout cannot sit in front of the 10 min crypto observation. Crypto used to be last in the config order.
- HTTP connect 3 s, read 10 s, backoff 2 s then 8 s, `Retry-After` capped at 15 s. No 429 or retry appeared in the two parsed logs. The cap is a bound. Worst case for one call is about a minute, not the previous 20 s timeouts plus a 120 s `Retry-After`.
- `prices.json` and `status.json` are replaced via a temp file. `status.json` has the cycle and the groups, not the 57 quotes. The watchdog prints the same facts from `prices.json` and does not fetch.
- Actions `run-name` is `watchdog` or `collector`. The long job is named `collector`. A watchdog run's skipped collector is the success path. The step summary lists one line per cycle and says the job length is sleep.

The long loop stays. A chain of short `workflow_dispatch` jobs would release the runner
between ticks (the measured duty cycle is well under 1% of the job), but each tick would
depend on a dispatch, and the only backstop is a cron that showed up about 6 times in
47 h. One dispatch per 5 h 40 min is the smaller outage window. Hiding the job duration
or forcing the check green would not make the requests faster. They are already short.

There is no assistant-box trigger, no third-party scheduler, and no site deploy in this
change. The site keeps reading `prices.json` through the existing rewrite.

## Expected freshness after the next collector is on this code

| Group | While the session is open | After the close | Alarm |
|---|---|---|---|
| crypto | observation every 10 min (health fails past 12 min), history row even if the price is flat | — | `failed` if the origin errors; `stale` if `fetched_at` is older than 12 min. `quote_time` may be a few seconds older than `fetched_at`. |
| US, HK, A | every 10 min (health past 12 min) | one fetch, then `closed` | `closed` is normal. HK `source_lag_s` around 900 is the feed, not a missed slot. |
| futures / FX | every 15 min (health past 17 min), including through the Globex evening session | one fetch +30 min, then the daily break | 05:47→07:02 Beijing gaps of that shape are the break |

`health` stored in the file is as of `cycle.ran_at`. The watchdog recomputes it from
`fetched_at` when it runs. Between writes (at most one crypto interval when nothing
else is due) trust `fetched_at`, not a frozen `health: ok`.

## Hand-over without overlap or a history hole

This patch does not merge, cancel, or deploy. The collector that is already running
checked out the old commit and will keep that code until the job exits.

1. Leave the active collector alone. Cancelling it drops the ticks until a watchdog
   happens to be delivered (measured in hours, not 30 min) and can cut a commit that
   is in flight.
2. Merge to `main` when you want this code. The in-flight job does not reload it.
3. Its last step dispatches `workflow_dispatch` on `--ref main`. The new run's
   collector waits on `live-prices-loop` until the old job exits, then checks out
   `main`. Same group name is required for that wait. Two writers do not run.
4. Do not start a manual `mode=loop` "to be safe" while one is active or queued. It
   waits and then runs another full 340 min, back to back, which is extra runner time
   and an extra hand-over. `mode=watchdog` only checks.
5. The first new cycle rewrites `prices.json` with the extra fields and adds
   `data/live/status.json`. History files stay append-only; a repeated minute is not
   written twice. Old rows are not rewritten.
6. Rollback is a revert on `main`. The following hand-over checks out that tree. The
   same "do not cancel the current job" rule applies.
7. The site does not need a redeploy. It already reads `prices.json`. New keys are
   optional. `quote_time` for BTC/ETH becomes the trade time, or null if the ticker
   was down, instead of a copy of the fetch clock.

## Limits that remain

- GitHub can still drop the watchdog for many hours. A dead collector is down until
  the next delivered cron or a manual `mode=loop`.
- The runner is occupied ~24 h/day. That is intentional.
- A total origin outage can spend up to ~60 s on one call. Tencent groups are serial
  with each other; Coinbase overlaps them but its own two products stay serial.
- HK (and any other Tencent print) can be minutes behind `fetched_at`. We do not
  have another redistribution-safe source in this repo.
- Futures `calendar_valid` stays false until a holiday table is added. The feed is
  still fetched on the weekday/Globex rules. That is not a fetch failure.
- `status.json` is updated when the document changes (a due cycle, a failure, or a
  session flag change), not on a pure no-op tick. The Actions log has every tick.
- One-shot HTTP numbers above are not runner measurements and are not a before/after.
