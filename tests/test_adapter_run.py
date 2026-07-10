import json
import os
import base64
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.runintent import RUN_INTENT_PAYLOAD_TYPE
from witnessd.signing import verify_dsse


def _fake_codex(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"message\",\"text\":\"done\"}}'\n"
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
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"command\":\"write code\"}}'\n"
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
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"command\":\"update tracked\"}}'\n"
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


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestAdapterRun(unittest.TestCase):
    def test_happy_path_emits_valid_receipt(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

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
                allowed_touched_files=["noop.txt"],
            )

            self.assertEqual(validate_runner_receipt(out["runner_receipt"]), [])
            self.assertEqual(out["runner_receipt"]["runner_kind"], "codex-cli")
            self.assertEqual(out["status_axis"]["assurance"], "evidence-pending")
            run_intent_path = pathlib.Path(out["evidence_dir"], "run-intent.json")
            self.assertTrue(run_intent_path.exists())
            run_intent_artifact = json.loads(run_intent_path.read_text(encoding="utf-8"))
            envelope = run_intent_artifact["dsse_envelope"]
            self.assertEqual(envelope["payloadType"], RUN_INTENT_PAYLOAD_TYPE)
            self.assertTrue(verify_dsse(envelope, out["public_key_path"]))
            intent = json.loads(base64.b64decode(envelope["payload"]).decode("utf-8"))
            self.assertEqual(intent["run_id"], "t")
            self.assertEqual(intent["allowed_paths"], ["noop.txt"])
            self.assertEqual(intent["provider"]["name"], "codex")
            subject_names = [
                item["name"] for item in out["bundle"]["statement"]["subject"]
            ]
            self.assertIn("run-intent", subject_names)

    def test_codex_uses_isolated_state_namespace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)
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
                    allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                )
            finally:
                if old_codex_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex_home

            used_home = pathlib.Path(sandbox, "codex-home.txt").read_text(
                encoding="utf-8"
            ).strip()
            self.assertTrue(
                os.path.realpath(used_home).startswith(
                    os.path.realpath(os.path.join(root, ".witnessd"))
                )
            )
            self.assertNotEqual(used_home, outside_codex_home)

    def test_adapter_evidence_includes_generated_diff_patch(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

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
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("+def generated():", patch)
            self.assertIn("pkg/agent.py", patch)

    def test_codex_transcript_binding_is_relative_to_evidence_parent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

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
                evidence_dir=evidence_dir,
                allowed_touched_files=["noop.txt"],
            )

            receipt = out["runner_receipt"]
            self.assertIn("--json", receipt["invocation"])
            self.assertNotIn("--output-last-message", receipt["invocation"])
            self.assertEqual(receipt["transcript_path"], "evidence/verify.log")
            command_log = json.loads(
                pathlib.Path(root, "adapter-command.json").read_text(encoding="utf-8")
            )
            self.assertEqual(command_log["command"], receipt["invocation"])
            self.assertTrue(pathlib.Path(root, "adapter-transcript.txt").exists())
            self.assertIn(
                '"thread.started"',
                pathlib.Path(root, "adapter-transcript.txt").read_text(encoding="utf-8"),
            )

    def test_missing_allowlist_is_not_filled_from_observed_touches(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

            with patch("witnessd.adapter_run.probe_adapter_capability"), patch(
                "witnessd.adapter_run._run_adapter",
                return_value=SimpleNamespace(
                    command_receipts=[
                        {
                            "command": ["fake-adapter"],
                            "exit_code": 0,
                            "stdout": "",
                            "stderr": "",
                        }
                    ],
                    touched_files=["touched.txt"],
                    test_output={"status": "not-run"},
                    invocation=["fake-adapter"],
                    runner_kind="fake-adapter",
                ),
            ):
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
                    evidence_dir=evidence_dir,
                )

            manifest = json.loads(
                pathlib.Path(evidence_dir, "capture-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["allowed_touched_files"], [])
            self.assertIn(
                "touched.txt",
                manifest["observer_capture"]["touched_files"],
            )

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
                allowed_touched_files=["tracked.txt"],
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("diff --git a/tracked.txt b/tracked.txt", patch)
            self.assertIn("+updated", patch)

    def test_route_exhausted_ends_blocked_not_silent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

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
            _init_repo(sandbox)

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
