import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.runlog import append_runlog
from witnessd.scheduler import Scheduler


class TestScheduler(unittest.TestCase):
    def test_reconcile_skips_completed_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            for lane in ("L1", "L2"):
                append_runlog(log, run_id="R1", event="dispatch", payload={"lane_id": lane})
            append_runlog(
                log,
                run_id="R1",
                event="exit",
                payload={"lane_id": "L1", "exit_code": 0},
            )
            scheduler = Scheduler(log, run_id="R1")

            pending = [packet["lane_id"] for packet in scheduler.reconcile()]

            self.assertEqual(pending, ["L2"])

    def test_reconcile_ignores_other_runs(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            append_runlog(log, run_id="R1", event="dispatch", payload={"lane_id": "L1"})
            append_runlog(log, run_id="R2", event="dispatch", payload={"lane_id": "L2"})

            scheduler = Scheduler(log, run_id="R1")

            self.assertEqual(scheduler.reconcile(), [{"lane_id": "L1"}])


if __name__ == "__main__":
    unittest.main()
