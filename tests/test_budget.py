import os
import tempfile
import unittest

from witnessd.budget import BudgetExceededError, CostBreaker
from witnessd.eventlog import EventLog


class TestBudget(unittest.TestCase):
    def _mk(self, directory: str, **kwargs) -> CostBreaker:
        return CostBreaker(
            log=EventLog(os.path.join(directory, "runlog.jsonl")),
            **kwargs,
        )

    def test_charge_records_measured_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            breaker = self._mk(
                directory, max_tokens=1000, max_usd=1.0, max_depth=3
            )

            breaker.charge(task_id="t", tokens=100, usd=0.1)

            self.assertEqual(breaker.spent_tokens, 100)
            self.assertEqual(breaker.spent_usd, 0.1)

    def test_predict_over_hard_cap_blocks_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            breaker = self._mk(
                directory, max_tokens=1000, max_usd=1.0, max_depth=3
            )

            with self.assertRaises(BudgetExceededError) as cm:
                breaker.check_can_spawn(
                    task_id="t",
                    predicted_tokens=2000,
                    predicted_usd=0.1,
                    depth=1,
                )

            self.assertEqual(cm.exception.metric, "tokens")
            self.assertEqual(cm.exception.code, "ERR_WITNESSD_BUDGET_EXCEEDED")

    def test_depth_budget_rejects_deep_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            breaker = self._mk(
                directory, max_tokens=10**9, max_usd=10**9, max_depth=2
            )

            with self.assertRaises(BudgetExceededError) as cm:
                breaker.check_can_spawn(
                    task_id="t",
                    predicted_tokens=1,
                    predicted_usd=0.0,
                    depth=3,
                )

            self.assertEqual(cm.exception.metric, "depth")

    def test_exceed_emits_budget_exceeded_event(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(os.path.join(directory, "runlog.jsonl"))
            breaker = CostBreaker(
                log=log, max_tokens=50, max_usd=1.0, max_depth=3
            )

            with self.assertRaises(BudgetExceededError):
                breaker.check_can_spawn(
                    task_id="t",
                    predicted_tokens=100,
                    predicted_usd=0.0,
                    depth=1,
                )

            self.assertIn("budget_exceeded", [event["event"] for event in log.read()])


if __name__ == "__main__":
    unittest.main()
