import os
import signal
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.liveness import HEARTBEAT_TTL_SECONDS, derive_liveness
from witnessd.supervisor import RegionLockError, WorkerSupervisor


class TestSupervisor(unittest.TestCase):
    def test_exit_code_captured_via_wait(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            handle = sup.spawn(lane_id="L1", argv=["sh", "-c", "exit 3"], runner_uid=1002)

            code = sup.wait(handle)

            self.assertEqual(code, 3)
            self.assertTrue(
                any(
                    record["event"] == "exit"
                    and record["payload"]["exit_code"] == 3
                    and record["payload"]["lane_id"] == "L1"
                    for record in log.read()
                )
            )

    def test_kill_flips_projection_to_not_active(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            handle = sup.spawn(
                lane_id="L1",
                argv=["sh", "-c", "sleep 30"],
                runner_uid=1002,
            )

            os.kill(handle.pid, signal.SIGKILL)
            sup.wait(handle)

            state = derive_liveness(
                log.read(),
                now_monotonic=HEARTBEAT_TTL_SECONDS + 1,
            )
            self.assertNotEqual(state.get("L1"), "active")

    def test_overlapping_region_lock_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")

            sup.claim_region("L1", ["src/a.py"])

            with self.assertRaises(RegionLockError):
                sup.claim_region("L2", ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
