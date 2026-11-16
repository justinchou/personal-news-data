import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

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
        self.assertEqual(sleeps, [7, 20])


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
        doc, done = live.run(t, prev={}, fetch=self.fake(log))
        self.assertIn("cn_equity", done)
        self.assertIn("hk_equity", done)
        self.assertIn("us_equity", done)  # no previous data → seed with the post-close fetch
        self.assertIn("futures_fx", done)
        self.assertEqual(doc["quotes"]["600900"]["price"], 2.0)
        self.assertEqual(doc["generated_at"], "2026-11-17T10:00:00+08:00")
        # 5 minutes later nothing is due
        doc2, done2 = live.run(datetime(2026, 11, 17, 10, 5, tzinfo=BJ), prev=doc, fetch=self.fake(log))
        self.assertEqual(done2, [])
        self.assertEqual(doc2["generated_at"], doc["generated_at"])
        # 10 minutes later: equities open again; US already has its post-close fetch; Globex is on 15 min
        _, done3 = live.run(datetime(2026, 11, 17, 10, 10, tzinfo=BJ), prev=doc2, fetch=self.fake(log))
        self.assertEqual(set(done3), {"hk_equity", "cn_equity"})

    def test_failure_keeps_previous(self):
        t = datetime(2026, 11, 17, 10, 0, tzinfo=BJ)
        doc, _ = live.run(t, prev={}, fetch=self.fake([]))

        def boom(source, codes, now):
            raise RuntimeError("down")
        doc2, done = live.run(datetime(2026, 11, 17, 10, 20, tzinfo=BJ), prev=doc, fetch=boom)
        self.assertEqual(done, [])
        self.assertEqual(doc2["quotes"]["600900"]["price"], 2.0)
        self.assertIn("down", doc2["groups"]["cn_equity"]["error"])

    def test_force(self):
        doc, done = live.run(datetime(2026, 11, 21, 12, 0, tzinfo=BJ), prev={}, force={"all"}, fetch=self.fake([]))  # Saturday
        self.assertEqual(set(done), {"us_equity", "hk_equity", "cn_equity", "futures_fx", "crypto"})
        json.dumps(doc, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
