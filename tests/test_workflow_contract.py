import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "live-prices.yml"
LOOP = ROOT / "scripts" / "live_loop.sh"
GUARD = ROOT / "scripts" / "loop_guard.py"


class WorkflowContract(unittest.TestCase):
    def test_collector_identity_and_single_writer(self):
        text = WF.read_text(encoding="utf-8")
        # Renaming the group would let a new run overlap the collector that is already in flight.
        self.assertIn("group: live-prices-loop", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('cron: "11,41 * * * *"', text)
        self.assertIn("timeout-minutes: 355", text)
        self.assertIn("run-name:", text)
        self.assertIn("watchdog", text)
        self.assertIn("collector", text)
        self.assertIn("gh workflow run live-prices.yml --repo", text)
        self.assertIn("--ref main", text)

    def test_loop_logs_sleep_separately_from_the_fetch(self):
        text = LOOP.read_text(encoding="utf-8")
        self.assertIn("tick_sleep_s", text)
        self.assertIn("GITHUB_STEP_SUMMARY", text)
        self.assertIn("git add data", text)

    def test_watchdog_labels_a_skip(self):
        text = GUARD.read_text(encoding="utf-8")
        self.assertIn('"role": "watchdog"', text)
        self.assertIn("this run does not fetch", text)
        self.assertIn("summarize_prices", text)


if __name__ == "__main__":
    unittest.main()
