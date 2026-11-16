import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from livedata import market_calendar as mc

BJ = ZoneInfo("Asia/Shanghai")


def bj(*a):
    return datetime(*a, tzinfo=BJ)


class T(unittest.TestCase):
    def test_us_dst_shift_in_beijing_time(self):
        self.assertEqual(mc.status("us_equity", bj(2026, 1, 12, 20, 0))["next_open"]["bj"], "2026-01-12 22:30")  # EST
        self.assertEqual(mc.status("us_equity", bj(2026, 7, 13, 20, 0))["next_open"]["bj"], "2026-07-13 21:30")  # EDT
        self.assertTrue(mc.status("us_equity", bj(2026, 7, 13, 22, 0))["open"])

    def test_us_holiday_and_early_close(self):
        self.assertFalse(mc.status("us_equity", bj(2026, 11, 26, 23, 0))["open"])  # Thanksgiving
        s = mc.status("us_equity", bj(2026, 11, 27, 23, 0))
        self.assertTrue(s["open"])
        self.assertEqual(s["next_close"]["bj"], "2026-11-28 02:00")  # 13:00 ET early close

    def test_cn_lunch_break_and_holiday(self):
        self.assertTrue(mc.status("cn_equity", bj(2026, 11, 17, 10, 0))["open"])
        lunch = mc.status("cn_equity", bj(2026, 11, 17, 12, 0))
        self.assertFalse(lunch["open"])
        self.assertEqual(lunch["next_open"]["bj"], "2026-11-17 13:00")
        self.assertFalse(mc.status("cn_equity", bj(2026, 10, 5, 10, 0))["open"])  # 国庆
        self.assertEqual(mc.status("cn_equity", bj(2026, 10, 5, 10, 0))["next_open"]["bj"], "2026-10-08 09:30")

    def test_hk_full_and_half_day(self):
        self.assertTrue(mc.status("hk_equity", bj(2026, 11, 17, 12, 30))["open"])  # extended morning session
        self.assertFalse(mc.status("hk_equity", bj(2026, 12, 24, 13, 0))["open"])  # half day
        self.assertFalse(mc.status("hk_equity", bj(2026, 10, 19, 10, 0))["open"])  # Chung Yeung holiday

    def test_globex_weekend_and_daily_break(self):
        self.assertFalse(mc.status("cme_globex", bj(2026, 11, 21, 12, 0))["open"])  # Saturday
        self.assertFalse(mc.status("cme_globex", bj(2026, 11, 17, 6, 30))["open"])  # 17:30 ET break
        self.assertTrue(mc.status("cme_globex", bj(2026, 11, 17, 7, 30))["open"])
        sun = mc.status("cme_globex", bj(2026, 11, 22, 12, 0))
        self.assertEqual(sun["next_open"]["bj"], "2026-11-23 07:00")  # Sun 18:00 ET

    def test_crypto_always_open(self):
        self.assertTrue(mc.status("crypto", bj(2026, 11, 21, 3, 0))["open"])

    def test_calendar_validity_flag(self):
        self.assertTrue(mc.status("cn_equity", bj(2026, 12, 1, 10, 0))["calendar_valid"])
        self.assertFalse(mc.status("cn_equity", bj(2027, 1, 4, 10, 0))["calendar_valid"])

    def test_tiered_should_fetch(self):
        t = bj(2026, 11, 17, 10, 0)
        self.assertTrue(mc.should_fetch("cn_equity", t, None))
        self.assertFalse(mc.should_fetch("cn_equity", t, bj(2026, 11, 17, 9, 55)))
        self.assertTrue(mc.should_fetch("cn_equity", t, bj(2026, 11, 17, 9, 50)))
        self.assertTrue(mc.should_fetch("cn_equity", t, bj(2026, 11, 17, 9, 51, 30)))  # late cron start tolerated
        self.assertFalse(mc.should_fetch("crypto", t, bj(2026, 11, 17, 9, 55)))  # 10 minutes, 24/7
        self.assertTrue(mc.should_fetch("crypto", bj(2026, 11, 21, 3, 0), bj(2026, 11, 21, 2, 50)))  # Saturday night too
        self.assertTrue(mc.should_fetch("cn_equity", bj(2026, 11, 17, 15, 16), bj(2026, 11, 17, 14, 59)))  # once after close
        self.assertFalse(mc.should_fetch("cn_equity", bj(2026, 11, 17, 18, 0), bj(2026, 11, 17, 15, 16)))
        self.assertFalse(mc.should_fetch("cn_equity", bj(2026, 11, 21, 10, 0), bj(2026, 11, 20, 15, 16)))  # weekend


if __name__ == "__main__":
    unittest.main()
