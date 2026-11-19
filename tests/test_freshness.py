import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from livedata import freshness, market_calendar as mc

BJ = ZoneInfo("Asia/Shanghai")


class Limits(unittest.TestCase):
    def test_limits_are_the_open_interval_plus_tolerance(self):
        tolerance_min = int(mc.TOLERANCE.total_seconds() // 60)
        for market, plan in mc.REFRESH.items():
            self.assertEqual(freshness.LIMIT_MIN[market], plan["open"] + tolerance_min)


class Judge(unittest.TestCase):
    def test_generated_at_does_not_make_an_old_quote_fresh(self):
        now = datetime(2026, 11, 19, 9, 32, tzinfo=BJ)
        meta = {
            "market": "crypto",
            "open": True,
            "fetched_at": (now - timedelta(minutes=40)).isoformat(timespec="seconds"),
        }
        self.assertEqual(freshness.judge("crypto", meta, now), "stale")

    def test_fresh_crypto_is_ok(self):
        now = datetime(2026, 11, 19, 9, 32, tzinfo=BJ)
        meta = {"market": "crypto", "open": True, "fetched_at": (now - timedelta(minutes=9)).isoformat()}
        self.assertEqual(freshness.judge("crypto", meta, now), "ok")

    def test_closed_market_is_not_a_failure(self):
        now = datetime(2026, 11, 19, 12, 0, tzinfo=BJ)
        meta = {
            "market": "us_equity",
            "open": False,
            "fetched_at": (now - timedelta(hours=7)).isoformat(),
        }
        self.assertEqual(freshness.judge("us_equity", meta, now), "closed")

    def test_error_is_failed_even_if_the_timestamp_is_new(self):
        now = datetime(2026, 11, 19, 9, 32, tzinfo=BJ)
        meta = {"market": "cn_equity", "open": True, "fetched_at": now.isoformat(), "error": "RuntimeError: down"}
        self.assertEqual(freshness.judge("cn_equity", meta, now), "failed")

    def test_unchanged_observation_is_ok(self):
        now = datetime(2026, 11, 19, 9, 32, tzinfo=BJ)
        meta = {"market": "crypto", "open": True, "status": "unchanged", "fetched_at": now.isoformat()}
        self.assertEqual(freshness.judge("crypto", meta, now), "ok")

    def test_source_lag_is_separate_from_health(self):
        fetched = "2026-11-19T10:30:00+08:00"
        quotes = {
            "0700-hk": {"group": "hk_equity", "quote_time": "2026-11-19T10:15:00+08:00", "fetched_at": fetched},
        }
        self.assertEqual(freshness.source_lag_s(quotes, "hk_equity", fetched), 15 * 60)
        now = datetime(2026, 11, 19, 10, 30, tzinfo=BJ)
        meta = {"market": "hk_equity", "open": True, "fetched_at": fetched}
        self.assertEqual(freshness.judge("hk_equity", meta, now), "ok")

    def test_summary_names_health(self):
        now = datetime(2026, 11, 19, 9, 32, tzinfo=BJ)
        text = freshness.summarize_prices({
            "generated_at": now.isoformat(),
            "groups": {"crypto": {"market": "crypto", "open": True, "fetched_at": (now - timedelta(minutes=30)).isoformat()}},
        }, now)
        self.assertIn("generated_at=", text)
        self.assertIn("crypto health=stale", text)


if __name__ == "__main__":
    unittest.main()
