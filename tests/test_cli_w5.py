import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


class TestCliW5(unittest.TestCase):
    def test_resume_requires_confirm_flag(self):
        with tempfile.TemporaryDirectory() as d:
            runlog = os.path.join(d, "runlog.jsonl")
            with redirect_stdout(io.StringIO()):
                code = main(["pause", "R1", "--runlog", runlog])
            self.assertEqual(code, 0)

            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["resume", "R1", "--runlog", runlog])
            self.assertNotEqual(code, 0)
            self.assertIn("ERR_WITNESSD_RESUME_UNCONFIRMED", err.getvalue())

    def test_install_unreadable_config_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, "config.bin")
            with open(config, "wb") as handle:
                handle.write(b"\x00\xff")
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w", encoding="utf-8") as handle:
                handle.write("x")
            dest = os.path.join(d, "dest")
            shim = os.path.join(d, "bin")
            os.makedirs(dest)
            os.makedirs(shim)

            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "install",
                        "--payload",
                        payload,
                        "--dest",
                        dest,
                        "--config",
                        config,
                        "--shim-dir",
                        shim,
                        "--version",
                        "v2",
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertIn("ERR_WITNESSD_CONFIG_UNREADABLE", err.getvalue())
            self.assertEqual(os.listdir(shim), [])

    def test_install_with_paused_runlog_refuses_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, "config.json")
            with open(config, "w", encoding="utf-8") as handle:
                json.dump({"ok": True}, handle)
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w", encoding="utf-8") as handle:
                handle.write("x")
            dest = os.path.join(d, "dest")
            shim = os.path.join(d, "bin")
            runlog = os.path.join(d, "runlog.jsonl")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["pause", "R1", "--runlog", runlog]), 0)

            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "install",
                        "--payload",
                        payload,
                        "--dest",
                        dest,
                        "--config",
                        config,
                        "--shim-dir",
                        shim,
                        "--version",
                        "v2",
                        "--runlog",
                        runlog,
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertIn("ERR_WITNESSD_PAUSED", err.getvalue())
            self.assertFalse(os.path.exists(os.path.join(dest, "v2.txt")))

    def test_install_uses_default_state_runlog_when_runlog_not_supplied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / "config.json"
            config.write_text(json.dumps({"ok": True}), encoding="utf-8")
            payload = root / "payload.txt"
            payload.write_text("x", encoding="utf-8")
            dest = root / "dest"
            shim = root / "bin"
            dest.mkdir()
            shim.mkdir()
            runlog = root / ".witnessd" / "runlog.jsonl"
            runlog.parent.mkdir()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["pause", "R1", "--runlog", str(runlog)]), 0)

            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                err = io.StringIO()
                with redirect_stderr(err):
                    code = main(
                        [
                            "install",
                            "--payload",
                            str(payload),
                            "--dest",
                            str(dest),
                            "--config",
                            str(config),
                            "--shim-dir",
                            str(shim),
                            "--version",
                            "v2",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

            self.assertNotEqual(code, 0)
            self.assertIn("ERR_WITNESSD_PAUSED", err.getvalue())
            self.assertFalse((dest / "v2.txt").exists())

    def test_faultkit_pause_race_cli_writes_runlog(self):
        with tempfile.TemporaryDirectory() as d:
            runlog = os.path.join(d, "runlog.jsonl")
            with redirect_stdout(io.StringIO()):
                code = main(["faultkit", "pause-race", "--runlog", runlog, "--run-id", "R1"])
            self.assertEqual(code, 0)
            with open(runlog, encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
            self.assertTrue(any(record.get("event") == "user_pause" for record in records))

    def test_kill_all_without_supervised_children_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            runlog = os.path.join(d, "runlog.jsonl")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["kill", "--all", "--runlog", runlog, "--run-id", "R1"])

            self.assertNotEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertFalse(result["all_confirmed_dead"])
            self.assertEqual(result["error_code"], "ERR_WITNESSD_KILL_NO_TARGETS")

    def test_kill_all_reconstructs_live_pid_from_runlog(self):
        from witnessd.eventlog import EventLog
        from witnessd.supervisor import WorkerSupervisor

        with tempfile.TemporaryDirectory() as d:
            runlog = os.path.join(d, "runlog.jsonl")
            log = EventLog(runlog)
            supervisor = WorkerSupervisor(log, run_id="R1")
            handle = supervisor.spawn(
                lane_id="L1",
                argv=["sh", "-c", "sleep 60"],
                runner_uid=os.getuid(),
            )

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["kill", "--all", "--runlog", runlog, "--run-id", "R1"])

            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["all_confirmed_dead"])
            self.assertEqual(handle.popen.wait(timeout=1), -15)

    def test_kill_all_refuses_broken_runlog_before_targeting_pid(self):
        from witnessd.eventlog import EventLog
        from witnessd.supervisor import WorkerSupervisor

        with tempfile.TemporaryDirectory() as d:
            runlog = os.path.join(d, "runlog.jsonl")
            log = EventLog(runlog)
            supervisor = WorkerSupervisor(log, run_id="R1")
            handle = supervisor.spawn(
                lane_id="L1",
                argv=["sh", "-c", "sleep 60"],
                runner_uid=os.getuid(),
            )
            try:
                lines = Path(runlog).read_text(encoding="utf-8").splitlines()
                record = json.loads(lines[0])
                record["event_hash"] = "0" * 64
                Path(runlog).write_text(json.dumps(record) + "\n", encoding="utf-8")

                err = io.StringIO()
                with redirect_stderr(err):
                    code = main(["kill", "--all", "--runlog", runlog, "--run-id", "R1"])

                self.assertNotEqual(code, 0)
                self.assertIn("runlog: broken_at=0", err.getvalue())
                self.assertIsNone(handle.popen.poll())
            finally:
                handle.popen.terminate()
                handle.popen.wait(timeout=1)


if __name__ == "__main__":
    unittest.main()
