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


def _fake_codex_writes_env_and_code(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "printf '%s\\n' \"$CODEX_HOME\" > codex-home.txt\n"
        "mkdir -p pkg\n"
        "cat > pkg/agent.py <<'PY'\n"
        "def generated():\n"
        "    return 'agent generated code'\n"
        "PY\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo wrote code >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_stages_tracked_change(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "printf 'updated\\n' > tracked.txt\n"
        "git add tracked.txt\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo staged tracked change >> \"$out\"\n"
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

    def test_codex_uses_isolated_state_namespace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            subprocess.run(["git", "init", "-q", sandbox], check=True)
            outside_codex_home = os.path.join(root, "operator-codex-home")
            os.makedirs(outside_codex_home)

            old_codex_home = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = outside_codex_home
            try:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex_writes_env_and_code(bindir),
                )
            finally:
                if old_codex_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex_home

            used_home = pathlib.Path(sandbox, "codex-home.txt").read_text(
                encoding="utf-8"
            ).strip()
            self.assertTrue(used_home.startswith(os.path.join(root, ".witnessd")))
            self.assertNotEqual(used_home, outside_codex_home)

    def test_adapter_evidence_includes_generated_diff_patch(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            subprocess.run(["git", "init", "-q", sandbox], check=True)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("+def generated():", patch)
            self.assertIn("pkg/agent.py", patch)

    def test_adapter_evidence_includes_staged_tracked_diff_patch(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            subprocess.run(["git", "init", "-q", sandbox], check=True)
            pathlib.Path(sandbox, "tracked.txt").write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=sandbox, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=sandbox, check=True)
            subprocess.run(["git", "add", "tracked.txt"], cwd=sandbox, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=sandbox, check=True)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_stages_tracked_change(bindir),
                evidence_dir=evidence_dir,
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("diff --git a/tracked.txt b/tracked.txt", patch)
            self.assertIn("+updated", patch)

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
