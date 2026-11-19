import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from livedata import live, sources

BJ = ZoneInfo("Asia/Shanghai")
SAMPLE = (
    'v_hf_CL="88.14,-0.48,88.18,88.20,89.49,87.59,19:24:01,88.56,88.65,0,6,7,2026-11-16,纽约原油";\n'
    'v_whUSDCNY="310~美元人民币~USDCNY~6.6671~0~20261116192336~6.6710~6.6704~6.6722~6.6647~6.6671~6.6672~-0.0039~-0.06";\n'
    'v_usNVDA="200~英伟达~NVDA.OQ~162.85~166.19~169.91~1~0~0~161.00~8800~0~0~0~0~0~0~0~0~161.04~100~0~0~0~0~0~0~0~0~~2026-11-13 16:00:01~-3.34~-2.01";\n'
    'v_hk00700="100~腾讯控股~00700~390.400~386.600~386.600~1~0~0~390.400~0~0~0~0~0~0~0~0~0~390.400~0~0~0~0~0~0~0~0~0~1~2026/11/16 16:08:18~3.800~0.98";\n'
    'v_sh600900="1~长江电力~600900~30.49~29.91~29.86~1~2~3~30.48~294~30.47~1692~30.46~204~30.45~493~30.44~156~30.49~1118~30.50~28169~30.51~2060~30.52~1644~30.53~1296~~20261116161456~0.58~1.94";\n'
    'v_pv_none_match="1";'
)


class Parse(unittest.TestCase):
    def test_parse_all_kinds(self):
        q = sources.parse_tencent(SAMPLE)
        self.assertEqual(set(q), {"hf_CL", "whUSDCNY", "usNVDA", "hk00700", "sh600900"})
        self.assertEqual(q["usNVDA"]["quote_time"], "2026-11-14T05:00:01+08:00")  # 16:00 ET → Beijing
        self.assertEqual(q["hk00700"]["quote_time"], "2026-11-16T16:08:18+08:00")
        self.assertEqual(q["sh600900"]["quote_time"], "2026-11-16T16:14:56+08:00")
        self.assertAlmostEqual(q["hf_CL"]["change_pct"], -0.474, places=2)
        self.assertAlmostEqual(q["whUSDCNY"]["prev_close"], 6.6710, places=4)
        self.assertAlmostEqual(q["sh600900"]["change_pct"], 1.939, places=2)

    def test_backoff_then_give_up(self):
        calls, sleeps = [], []

        class R:
            status_code, headers = 429, {"Retry-After": "7"}

        class S:
            def get(self, url, **kw):
                calls.append(url)
                return R()
        with self.assertRaises(Exception):
            sources.get("https://example.invalid", sleep=sleeps.append, session=S())
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [7, 8])  # Retry-After 7, then backoff 8s (Retry-After does not raise it)

    def test_retry_after_is_capped(self):
        sleeps = []

        class R:
            status_code, headers = 429, {"Retry-After": "500"}

        class S:
            def get(self, url, **kw):
                return R()

        with self.assertRaises(Exception):
            sources.get("https://example.invalid", sleep=sleeps.append, session=S())
        self.assertEqual(sleeps, [15, 15])


class Run(unittest.TestCase):
    def fake(self, log):
        def f(source, codes, now):
            log.append(source)
            if source == "coinbase":
                return {c: {"price": 1.0, "prev_close": 1.0, "change_pct": 0.0, "quote_time": now.isoformat()} for c in codes}
            return {c: {"price": 2.0, "prev_close": 1.0, "change_pct": 100.0, "quote_time": now.isoformat(), "name": "x"} for c in codes}
        return f

    def test_session_gating(self):
        log = []
        t = datetime(2026, 11, 17, 10, 0, tzinfo=BJ)  # Tue: A/HK open, US closed, Globex open
        doc, done, _ = live.run(t, prev={}, fetch=self.fake(log))
        self.assertIn("cn_equity", done)
        self.assertIn("hk_equity", done)
        self.assertIn("us_equity", done)  # no previous data → seed with the post-close fetch
        self.assertIn("futures_fx", done)
        self.assertEqual(doc["quotes"]["600900"]["price"], 2.0)
        self.assertEqual(doc["generated_at"], "2026-11-17T10:00:00+08:00")
        # 5 minutes later nothing is due
        doc2, done2, rep2 = live.run(datetime(2026, 11, 17, 10, 5, tzinfo=BJ), prev=doc, fetch=self.fake(log))
        self.assertEqual(done2, [])
        self.assertEqual(doc2["generated_at"], doc["generated_at"])
        self.assertEqual(rep2["groups"]["crypto"]["status"], "not_due")
        self.assertEqual(rep2["groups"]["us_equity"]["status"], "closed")
        self.assertEqual(rep2["groups"]["us_equity"]["health"], "closed")
        self.assertFalse(rep2["groups"]["crypto"]["due"])
        # 10 minutes later: equities and crypto due again; US already has its post-close fetch; Globex is on 15 min
        _, done3, _ = live.run(datetime(2026, 11, 17, 10, 10, tzinfo=BJ), prev=doc2, fetch=self.fake(log))
        self.assertEqual(set(done3), {"hk_equity", "cn_equity", "crypto"})

    def test_failure_keeps_previous(self):
        t = datetime(2026, 11, 17, 10, 0, tzinfo=BJ)
        doc, _, _ = live.run(t, prev={}, fetch=self.fake([]))

        def boom(source, codes, now):
            raise RuntimeError("down")
        doc2, done, rep = live.run(datetime(2026, 11, 17, 10, 20, tzinfo=BJ), prev=doc, fetch=boom)
        self.assertEqual(done, [])
        self.assertEqual(doc2["quotes"]["600900"]["price"], 2.0)
        self.assertEqual(doc2["quotes"]["600900"]["fetched_at"], doc["quotes"]["600900"]["fetched_at"])
        self.assertEqual(doc2["generated_at"], doc["generated_at"])
        self.assertEqual(doc2["cycle"]["ran_at"], "2026-11-17T10:20:00+08:00")
        self.assertNotEqual(doc2["cycle"]["ran_at"], doc2["generated_at"])
        self.assertEqual(doc2["groups"]["cn_equity"]["status"], "failed")
        self.assertEqual(rep["groups"]["cn_equity"]["health"], "failed")
        self.assertIn("down", doc2["groups"]["cn_equity"]["error"])

    def test_crypto_history_append_only(self):
        import tempfile
        from pathlib import Path
        t = datetime(2026, 11, 21, 20, 0, tzinfo=BJ)  # Saturday: only crypto is due after seeding
        doc, done, _ = live.run(t, prev={}, fetch=self.fake([]))
        self.assertIn("crypto", done)
        with tempfile.TemporaryDirectory() as d:
            p = live.append_history(doc, Path(d))
            self.assertIsNone(live.append_history(doc, Path(d)))  # same timestamp not duplicated
            doc2, done2, _ = live.run(datetime(2026, 11, 21, 20, 10, tzinfo=BJ), prev=doc, fetch=self.fake([]))
            self.assertEqual(done2, ["crypto"])
            live.append_history(doc2, Path(d))
            self.assertEqual(p.read_text().splitlines(), ["time_bj,btc_usd,eth_usd", "2026-11-21T20:00,1.00,1.00", "2026-11-21T20:10,1.00,1.00"])

    def test_force(self):
        doc, done, _ = live.run(datetime(2026, 11, 21, 12, 0, tzinfo=BJ), prev={}, force={"all"}, fetch=self.fake([]))  # Saturday
        self.assertEqual(set(done), {"us_equity", "hk_equity", "cn_equity", "futures_fx", "crypto"})
        json.dumps(doc, ensure_ascii=False)


class Distinctions(unittest.TestCase):
    def fake(self, price):
        def f(source, codes, now):
            return {c: {"price": price, "prev_close": price, "change_pct": 0.0, "quote_time": now.isoformat()} for c in codes}
        return f

    def test_unchanged_price_is_not_a_failure_and_still_history(self):
        t = datetime(2026, 11, 21, 20, 0, tzinfo=BJ)
        doc, _, _ = live.run(t, prev={}, force={"crypto"}, fetch=self.fake(1.0))
        doc2, done, rep = live.run(t + timedelta(minutes=10), prev=doc, force={"crypto"}, fetch=self.fake(1.0))
        self.assertEqual(done, ["crypto"])
        self.assertEqual(rep["groups"]["crypto"]["status"], "unchanged")
        self.assertEqual(rep["groups"]["crypto"]["health"], "ok")
        self.assertTrue(rep["groups"]["crypto"]["due"])
        self.assertNotIn("error", doc2["groups"]["crypto"])
        self.assertEqual(doc2["quotes"]["btc"]["price"], 1.0)
        self.assertEqual(doc2["quotes"]["btc"]["fetched_at"], "2026-11-21T20:10:00+08:00")
        self.assertEqual(doc2["generated_at"], "2026-11-21T20:10:00+08:00")
        with tempfile.TemporaryDirectory() as d:
            live.append_history(doc, Path(d))
            path = live.append_history(doc2, Path(d))
            self.assertEqual(path.read_text().splitlines()[-1], "2026-11-21T20:10,1.00,1.00")

    def test_failed_group_stays_stale_when_sibling_updates_generated_at(self):
        t = datetime(2026, 11, 21, 12, 0, tzinfo=BJ)

        def seed(source, codes, now):
            return {c: {"price": 2.0, "prev_close": 1.0, "change_pct": 0.0, "quote_time": now.isoformat()} for c in codes}

        doc, _, _ = live.run(t, prev={}, force={"crypto", "us_equity"}, fetch=seed)

        def mixed(source, codes, now):
            if source == "coinbase":
                raise RuntimeError("coinbase down")
            return {c: {"price": 3.0, "prev_close": 1.0, "change_pct": 0.0, "quote_time": now.isoformat()} for c in codes}

        later = t + timedelta(minutes=30)
        doc2, done, rep = live.run(later, prev=doc, force={"crypto", "us_equity"}, fetch=mixed)
        self.assertEqual(done, ["us_equity"])
        self.assertEqual(doc2["generated_at"], "2026-11-21T12:30:00+08:00")
        self.assertEqual(doc2["groups"]["crypto"]["fetched_at"], "2026-11-21T12:00:00+08:00")
        self.assertEqual(doc2["quotes"]["btc"]["price"], 2.0)
        self.assertEqual(doc2["quotes"]["btc"]["fetched_at"], "2026-11-21T12:00:00+08:00")
        self.assertEqual(rep["groups"]["crypto"]["status"], "failed")
        self.assertEqual(rep["groups"]["crypto"]["health"], "failed")
        self.assertEqual(rep["groups"]["us_equity"]["status"], "fetched")
        # Saturday: the market is closed, so a successful observation is not an "open session" health check.
        self.assertEqual(rep["groups"]["us_equity"]["health"], "closed")
        self.assertNotEqual(doc2["generated_at"], doc2["groups"]["crypto"]["fetched_at"])

    def test_empty_payload_keeps_previous_quote(self):
        t = datetime(2026, 11, 21, 12, 0, tzinfo=BJ)
        doc, _, _ = live.run(t, prev={}, force={"crypto"}, fetch=self.fake(9.0))
        doc2, done, rep = live.run(t + timedelta(minutes=10), prev=doc, force={"crypto"}, fetch=lambda *a: {})
        self.assertEqual(done, [])
        self.assertEqual(doc2["quotes"]["btc"]["price"], 9.0)
        self.assertEqual(doc2["generated_at"], doc["generated_at"])
        self.assertEqual(rep["groups"]["crypto"]["status"], "failed")
        self.assertIn("empty", doc2["groups"]["crypto"]["error"])

    def test_closed_and_not_due_are_not_failures(self):
        t = datetime(2026, 11, 21, 20, 0, tzinfo=BJ)  # Saturday
        doc, _, _ = live.run(t, prev={}, fetch=self.fake(1.0))
        _, done, rep = live.run(t + timedelta(minutes=5), prev=doc, fetch=self.fake(1.0))
        self.assertEqual(done, [])
        self.assertEqual(rep["groups"]["us_equity"]["status"], "closed")
        self.assertEqual(rep["groups"]["us_equity"]["health"], "closed")
        self.assertEqual(rep["groups"]["crypto"]["status"], "not_due")
        self.assertEqual(rep["groups"]["crypto"]["health"], "ok")
        self.assertNotEqual(rep["groups"]["crypto"]["health"], "failed")

    def test_source_lag_does_not_fail_a_fresh_fetch(self):
        t = datetime(2026, 11, 17, 10, 30, tzinfo=BJ)
        lagging = (t - timedelta(minutes=15)).isoformat()

        def hk_slow(source, codes, now):
            return {c: {"price": 10.0, "prev_close": 9.0, "change_pct": 1.0, "quote_time": lagging} for c in codes}

        doc, _, rep = live.run(t, prev={}, force={"hk_equity"}, fetch=hk_slow)
        self.assertEqual(rep["groups"]["hk_equity"]["status"], "fetched")
        self.assertEqual(rep["groups"]["hk_equity"]["health"], "ok")
        self.assertGreaterEqual(rep["groups"]["hk_equity"]["source_lag_s"], 15 * 60 - 1)
        self.assertEqual(doc["groups"]["hk_equity"]["quote_time_origin"], "source")

    def test_tencent_batches_do_not_overlap_and_coinbase_may(self):
        # Handshake: the first Tencent call does not return until Coinbase has entered it,
        # and Coinbase does not enter until that Tencent call has started.
        tencent_inside = threading.Event()
        coinbase_inside = threading.Event()
        depth = {"n": 0, "max": 0}
        lock = threading.Lock()
        spans = []
        state = {"handshook": False, "overlap": False}

        def quotes(codes, now):
            return {c: {"price": 1.0, "prev_close": 1.0, "change_pct": 0.0, "quote_time": now.isoformat()} for c in codes}

        def f(source, codes, now):
            if source != "tencent":
                self.assertTrue(tencent_inside.wait(2))
                state["overlap"] = True
                coinbase_inside.set()
                return quotes(codes, now)
            with lock:
                depth["n"] += 1
                depth["max"] = max(depth["max"], depth["n"])
            try:
                if not state["handshook"]:
                    tencent_inside.set()
                    self.assertTrue(coinbase_inside.wait(2))
                    state["handshook"] = True
                t0 = time.monotonic()
                time.sleep(0.02)
                spans.append((t0, time.monotonic()))
            finally:
                with lock:
                    depth["n"] -= 1
            return quotes(codes, now)

        t = datetime(2026, 11, 21, 12, 0, tzinfo=BJ)
        iso = t.isoformat(timespec="seconds")
        # Recent fetched_at so only the forced groups run. Otherwise a cold start
        # also seeds every closed market and the span count is not just us+hk.
        prev = {"quotes": {}, "groups": {gid: {"fetched_at": iso} for gid in
                ("us_equity", "hk_equity", "cn_equity", "futures_fx", "crypto")}}
        live.run(t, prev=prev, force={"us_equity", "hk_equity", "crypto"}, fetch=f)
        self.assertTrue(state["overlap"])
        self.assertEqual(depth["max"], 1)
        self.assertEqual(len(spans), 2)
        self.assertLessEqual(spans[0][1], spans[1][0] + 0.02)

    def test_write_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "prices.json"
            self.assertTrue(live.write({"b": 1, "a": 2}, path))
            self.assertFalse(live.write({"a": 2, "b": 1}, path))
            self.assertEqual(json.loads(path.read_text()), {"a": 2, "b": 1})
            self.assertEqual(list(Path(d).glob("*.tmp")), [])


class CoinbaseTime(unittest.TestCase):
    def test_quote_time_is_the_trade_not_the_fetch_clock(self):
        now = datetime(2026, 11, 19, 9, 50, tzinfo=BJ)

        class Resp:
            def __init__(self, payload):
                self.status_code, self.headers, self.text = 200, {}, ""
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                return None

        class S:
            def get(self, url, **kw):
                self.timeout = kw.get("timeout")
                if url.endswith("/stats"):
                    return Resp({"last": "100", "open": "90"})
                return Resp({"price": "101.5", "time": "2026-11-19T01:42:20.875145373Z"})

        session = S()
        sources.begin_info()
        out = sources.coinbase(["BTC-USD"], now=now, session=session, sleep=lambda s: None)
        self.assertEqual(out["BTC-USD"]["price"], 101.5)  # ticker, not stats last
        self.assertEqual(out["BTC-USD"]["quote_time"], "2026-11-19T09:42:20+08:00")
        self.assertNotEqual(out["BTC-USD"]["quote_time"], now.isoformat(timespec="seconds"))
        self.assertEqual(session.timeout, (3.05, 10))
        self.assertEqual(sources.snapshot_info()["quote_time_origin"], "exchange")

    def test_ticker_failure_keeps_stats_price_and_records_origin(self):
        class Resp:
            status_code, headers, text = 200, {}, ""

            def json(self):
                return {"last": "100", "open": "90"}

            def raise_for_status(self):
                return None

        class S:
            def get(self, url, **kw):
                if url.endswith("/ticker"):
                    raise requests.ConnectionError("nope")
                return Resp()

        sources.begin_info()
        out = sources.coinbase(["BTC-USD"], now=datetime(2026, 11, 19, 9, 50, tzinfo=BJ), session=S(), sleep=lambda s: None)
        self.assertEqual(out["BTC-USD"]["price"], 100.0)
        self.assertIsNone(out["BTC-USD"]["quote_time"])
        info = sources.snapshot_info()
        self.assertEqual(info["quote_time_origin"], "unavailable")
        self.assertIn("ticker", info["origin_error"])
        self.assertGreaterEqual(info["attempts"], 4)  # stats once + ticker retries


if __name__ == "__main__":
    unittest.main()
