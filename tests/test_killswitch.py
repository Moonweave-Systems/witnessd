import os
import tempfile
import time
import unittest

from witnessd.eventlog import EventLog
from witnessd.killswitch import active_targets_from_runlog, kill_all
from witnessd.liveness import derive_liveness
from witnessd.supervisor import WorkerSupervisor


class TestKillSwitch(unittest.TestCase):
    def test_kill_all_without_known_children_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            supervisor = WorkerSupervisor(log, run_id="R1")

            result = kill_all(supervisor, log, run_id="R1", grace=0.01)

            self.assertFalse(result["killed"])
            self.assertFalse(result["all_confirmed_dead"])
            self.assertEqual(result["outcomes"], [])
            kill_events = [record for record in log.read() if record.get("event") == "kill"]
            self.assertEqual(len(kill_events), 1)
            self.assertEqual(kill_events[0].get("error_code"), "ERR_WITNESSD_KILL_NO_TARGETS")

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

    def test_active_targets_from_runlog_requires_pid_identity_match(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            supervisor = WorkerSupervisor(log, run_id="R1")
            handle = supervisor.spawn(
                lane_id="L1",
                argv=["sh", "-c", "sleep 60"],
                runner_uid=os.getuid(),
            )
            try:
                records = log.read()
                records[-1]["payload"]["pid_start_time"] = "definitely-not-this-process"

                self.assertEqual(active_targets_from_runlog(records), [])
            finally:
                handle.popen.terminate()
                handle.popen.wait(timeout=1)


if __name__ == "__main__":
    unittest.main()
