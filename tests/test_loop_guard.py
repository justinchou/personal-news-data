import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from loop_guard import blocking_runs  # noqa: E402


def run(i, event, status):
    return {"id": i, "event": event, "status": status}


class Guard(unittest.TestCase):
    def test_no_other_runs_starts(self):
        self.assertEqual(blocking_runs([run(10, "schedule", "in_progress"), run(5, "workflow_dispatch", "completed")], 10), [])

    def test_active_loop_blocks(self):
        b = blocking_runs([run(10, "schedule", "in_progress"), run(7, "workflow_dispatch", "in_progress")], 10)
        self.assertEqual([r["id"] for r in b], [7])

    def test_queued_next_loop_blocks(self):
        self.assertTrue(blocking_runs([run(9, "workflow_dispatch", "queued")], 10))

    def test_overlapping_watchdogs_older_wins(self):
        runs = [run(10, "schedule", "in_progress"), run(11, "schedule", "in_progress")]
        self.assertEqual(blocking_runs(runs, 10), [])               # older one starts the loop
        self.assertEqual([r["id"] for r in blocking_runs(runs, 11)], [10])  # newer one stands down

    def test_failed_loop_does_not_block(self):
        self.assertEqual(blocking_runs([run(7, "workflow_dispatch", "completed")], 10), [])


if __name__ == "__main__":
    unittest.main()
