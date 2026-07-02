import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

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


if __name__ == "__main__":
    unittest.main()
