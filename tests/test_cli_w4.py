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
from witnessd.model_policy import DEFAULT_MODEL_POLICY


def _fake_codex(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "printf 'ok\\n' > out.txt\n"
        "cat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"message\",\"text\":\"done\"}}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _init_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    pathlib.Path(path, "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


class TestCliW4(unittest.TestCase):
    def test_route_reports_model_policy_model(self):
        with tempfile.TemporaryDirectory() as root:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["route", "--root", root, "--tier", "quick"])

            decision = json.loads(out.getvalue())
            policy_models = {
                str(candidate["model"])
                for route in DEFAULT_MODEL_POLICY["routes"]
                if route["tier"] == "quick"
                for candidate in route["candidates"]
            }
            self.assertEqual(code, 0)
            self.assertIn(decision["model"], policy_models)
            self.assertFalse(decision["model"].startswith("gpt-5.3"))

    @unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
    def test_run_codex_adapter_emits_valid_receipt(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)
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
                        "--allow",
                        "out.txt",
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

    @unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
    def test_run_codex_adapter_without_allow_fails_structured(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)
            err = io.StringIO()

            with redirect_stderr(err):
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

            self.assertEqual(code, 1)
            self.assertIn("ERR_CODEX_ALLOWED_PATHS_REQUIRED", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())

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
            _init_repo(sandbox)
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
