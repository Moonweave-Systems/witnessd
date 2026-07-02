import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.router import RouteExhaustedError, TIER_CANDIDATES, route_model


def _read(log: EventLog) -> list[dict]:
    return log.read()


class TestRouter(unittest.TestCase):
    def test_returns_first_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(os.path.join(directory, "runlog.jsonl"))
            decision = route_model(
                task_id="t",
                tier="agentic",
                log=log,
                is_supported=lambda _model: True,
            )

            self.assertEqual(decision["model"], TIER_CANDIDATES["agentic"][0])
            self.assertEqual(decision["concurrency_key"], "t:agentic")
            self.assertFalse(decision["degraded"])

    def test_retry_on_model_not_supported_then_degrade(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(os.path.join(directory, "runlog.jsonl"))
            calls = []
            decision = route_model(
                task_id="t",
                tier="frontier",
                log=log,
                is_supported=lambda model: calls.append(model)
                or (model == TIER_CANDIDATES["frontier"][-1]),
            )

            self.assertEqual(decision["model"], TIER_CANDIDATES["frontier"][-1])
            self.assertTrue(decision["degraded"])
            self.assertEqual(calls, TIER_CANDIDATES["frontier"])
            events = [event["event"] for event in _read(log)]
            self.assertIn("model_not_supported", events)
            self.assertIn("route_selected", events)

    def test_exhausted_raises_blocked_not_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(os.path.join(directory, "runlog.jsonl"))
            with self.assertRaises(RouteExhaustedError) as cm:
                route_model(
                    task_id="t",
                    tier="quick",
                    log=log,
                    is_supported=lambda _model: False,
                )

            self.assertEqual(cm.exception.code, "ERR_WITNESSD_ROUTE_EXHAUSTED")
            self.assertIn("route_blocked", [event["event"] for event in _read(log)])


if __name__ == "__main__":
    unittest.main()
