from __future__ import annotations

import unittest

from witnessd.model_routing_benchmark import (
    MODEL_ROUTING_BENCHMARK_KIND,
    default_task_suite,
    plan_measurements,
)


class ModelRoutingBenchmarkTests(unittest.TestCase):
    def test_default_suite_routes_and_budgets_without_live_calls(self) -> None:
        suite = default_task_suite()

        self.assertEqual(len(suite), 24)
        payload = plan_measurements(suite[:3])

        self.assertEqual(payload["kind"], MODEL_ROUTING_BENCHMARK_KIND)
        self.assertFalse(payload["boundary"]["proof"])
        self.assertFalse(payload["boundary"]["assurance"])
        self.assertFalse(payload["boundary"]["benchmark_claim"])
        self.assertFalse(payload["live"])
        self.assertEqual(len(payload["tasks"]), 3)
        for task in payload["tasks"]:
            self.assertEqual(task["status"], "planned")
            self.assertIn(task["route"]["adapter"], {"codex", "agy"})
            self.assertIn("model", task["route"])
            self.assertGreater(task["budget"]["max_tokens"], 0)
            self.assertGreater(task["budget"]["max_usd"], 0.0)
            self.assertTrue(task["budget_compliant"])


if __name__ == "__main__":
    unittest.main()
