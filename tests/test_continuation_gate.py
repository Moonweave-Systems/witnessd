import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.pause import ERR_WITNESSD_PAUSED, PauseError, append_user_pause
from witnessd.scheduler import Scheduler
from witnessd.supervisor import WorkerSupervisor


class TestContinuationGate(unittest.TestCase):
    def test_spawn_refused_when_paused(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            supervisor = WorkerSupervisor(log, run_id="R1")
            append_user_pause(log, run_id="R1", source="cli")

            with self.assertRaises(PauseError) as cm:
                supervisor.spawn(lane_id="L1", argv=["sh", "-c", "true"], runner_uid=os.getuid())

            self.assertEqual(cm.exception.code, ERR_WITNESSD_PAUSED)
            self.assertFalse(any(record.get("event") == "spawn" for record in log.read()))

    def test_schedule_refused_when_paused(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            append_user_pause(log, run_id="R1", source="cli")
            scheduler = Scheduler(log, run_id="R1")
            supervisor = WorkerSupervisor(log, run_id="R1")

            with self.assertRaises(PauseError):
                scheduler.schedule(supervisor)


if __name__ == "__main__":
    unittest.main()
