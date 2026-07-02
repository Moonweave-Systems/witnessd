import os
import tempfile
import time
import unittest

from witnessd.eventlog import EventLog
from witnessd.killswitch import kill_all
from witnessd.liveness import derive_liveness
from witnessd.supervisor import WorkerSupervisor


class TestKillSwitch(unittest.TestCase):
    def test_kill_all_terminates_and_derives_dead(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            supervisor = WorkerSupervisor(log, run_id="R1")
            handle = supervisor.spawn(
                lane_id="L1",
                argv=["sh", "-c", "sleep 60"],
                runner_uid=os.getuid(),
            )

            result = kill_all(supervisor, log, run_id="R1", grace=0.05)

            self.assertTrue(result["killed"])
            self.assertIsNotNone(handle.popen.poll())
            self.assertTrue(any(record.get("event") == "kill" for record in log.read()))
            state = derive_liveness(log.read(), now_monotonic=time.monotonic() + 10_000)
            self.assertEqual(state.get("L1"), "dead")


if __name__ == "__main__":
    unittest.main()
