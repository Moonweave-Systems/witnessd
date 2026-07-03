import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from witnessd.eventlog import EventLog

ROOT = Path(__file__).resolve().parents[1]


class TestCliW2(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        depone_path = os.environ.get("WITNESSD_DEPONE_ROOT", str(ROOT.parent / "depone"))
        pythonpath = [str(ROOT), depone_path]
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        return subprocess.run(
            [sys.executable, "-m", "witnessd", *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def _zombie_runlog(self, path: str) -> None:
        log = EventLog(path)
        log.append(
            {
                "schema_version": "1.0",
                "kind": "witnessd-runlog-event",
                "run_id": "R1",
                "event": "spawn",
                "error_code": None,
                "ts_wall": "2026-01-01T00:00:00Z",
                "ts_monotonic": 0.0,
                "payload": {"lane_id": "L1"},
            }
        )
        log.append(
            {
                "schema_version": "1.0",
                "kind": "witnessd-runlog-event",
                "run_id": "R1",
                "event": "heartbeat",
                "error_code": None,
                "ts_wall": "2026-01-01T00:00:01Z",
                "ts_monotonic": 1.0,
                "payload": {"lane_id": "L1"},
            }
        )

    def test_verify_runlog_reports_ok(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runlog.jsonl")
            self._zombie_runlog(path)

            result = self._run("verify", "--runlog", path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("runlog: ok", result.stdout)

    def test_status_and_doctor_report_zombie_without_all_clear(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runlog.jsonl")
            self._zombie_runlog(path)

            status = self._run("status", "--runlog", path)
            doctor = self._run("doctor", "--runlog", path)

            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("zombie", status.stdout.lower())
            self.assertNotIn("all clear", status.stdout.lower())
            self.assertNotEqual(doctor.returncode, 0)
            self.assertIn("zombie", doctor.stdout.lower())
            self.assertNotIn("all clear", doctor.stdout.lower())

    def test_isolation_self_test_command(self):
        result = self._run("isolation", "--self-test")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_faultkit_zombie_hang_generates_runlog(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runlog.jsonl")

            generated = self._run("faultkit", "zombie-hang", "--runlog", path)
            status = self._run("status", "--runlog", path)

            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertIn("zombie", status.stdout.lower())

    def test_faultkit_crash_mid_toolcall_preserves_resume_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            before = os.path.join(d, "runlog-before.jsonl")
            after = os.path.join(d, "runlog-after.jsonl")
            session = os.path.join(d, "session.json")

            generated = self._run(
                "faultkit",
                "crash-mid-toolcall",
                "--runlog-before",
                before,
                "--runlog-after",
                after,
                "--session",
                session,
            )

            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertIn("evidence-pending", generated.stdout)
            with open(session, "r", encoding="utf-8") as handle:
                payload = __import__("json").load(handle)
            self.assertEqual(payload["run_state"], "evidence-pending")
            self.assertEqual(payload["idempotency_reapplied"], 0)
            self.assertEqual(payload["tool_call_cursor"], 1)


if __name__ == "__main__":
    unittest.main()
