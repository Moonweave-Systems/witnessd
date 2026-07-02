import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapter_run import LaneBlocked, run_adapter_lane


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


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestAdapterRun(unittest.TestCase):
    def test_happy_path_emits_valid_receipt(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
            )

            self.assertEqual(validate_runner_receipt(out["runner_receipt"]), [])
            self.assertEqual(out["runner_receipt"]["runner_kind"], "codex-cli")
            self.assertEqual(out["status_axis"]["assurance"], "evidence-pending")

    def test_route_exhausted_ends_blocked_not_silent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)

            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="x",
                    arm="direct",
                    tier="quick",
                    is_supported=lambda _model: False,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex(bindir),
                )

            self.assertEqual(cm.exception.reason, "route_blocked")
            runlog_path = os.path.join(root, ".witnessd", "runlog.jsonl")
            with open(runlog_path, encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle]
            self.assertIn("model_not_supported", [event["event"] for event in events])
            self.assertIn("route_blocked", [event["event"] for event in events])
            self.assertNotIn("VERIFIED", json.dumps(events))

    def test_budget_blowout_hard_stops(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)

            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="x",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 1, "max_usd": 1.0, "max_depth": 3},
                    predicted_tokens=10**6,
                    codex_binary=_fake_codex(bindir),
                )

            self.assertEqual(cm.exception.reason, "budget_exceeded")


if __name__ == "__main__":
    unittest.main()
