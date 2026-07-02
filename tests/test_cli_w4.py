import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.__main__ import main


def _fake_codex(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestCliW4(unittest.TestCase):
    @unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
    def test_run_codex_adapter_emits_valid_receipt(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)
            out = io.StringIO()

            with redirect_stdout(out):
                code = main(
                    [
                        "run",
                        "--adapter",
                        "codex",
                        "--root",
                        root,
                        "--runner-sandbox",
                        sandbox,
                        "--task-id",
                        "t",
                        "--arm",
                        "direct",
                        "--tier",
                        "agentic",
                        "--codex-binary",
                        _fake_codex(bindir),
                        "--",
                        "do X",
                    ]
                )

            self.assertEqual(code, 0)
            receipt_path = os.path.join(
                root, ".witnessd", "lanes", "t", "evidence", "runner-receipt.json"
            )
            with open(receipt_path, encoding="utf-8") as handle:
                receipt = json.load(handle)
            self.assertEqual(validate_runner_receipt(receipt), [])
            self.assertEqual(receipt["runner_kind"], "codex-cli")
            self.assertIn("evidence-pending", out.getvalue())

    def test_doctor_detects_state_contention(self):
        with tempfile.TemporaryDirectory() as root:
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "doctor",
                        "--root",
                        root,
                        "--external-worktree",
                        os.path.join(root, "repo"),
                    ]
                )

            self.assertEqual(code, 3)
            self.assertIn("ERR_WITNESSD_STATE_CONTENTION", err.getvalue())

    def test_faultkit_budget_blowout_records_event_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)
            out = io.StringIO()

            with redirect_stdout(out):
                code = main(
                    [
                        "faultkit",
                        "budget-blowout",
                        "--root",
                        root,
                        "--runner-sandbox",
                        sandbox,
                        "--codex-binary",
                        _fake_codex(bindir),
                        "--max-tokens",
                        "1",
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertIn("budget_exceeded", out.getvalue())
            with open(
                os.path.join(root, ".witnessd", "runlog.jsonl"), encoding="utf-8"
            ) as handle:
                events = [json.loads(line) for line in handle]
            self.assertIn("budget_exceeded", [event["event"] for event in events])
            self.assertNotIn("VERIFIED", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
