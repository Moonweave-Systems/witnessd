import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.isolation import UID_OBSERVER_LAUNCHED_ISOLATION_MODEL

from scripts import revalidate_w12
from scripts.revalidate_w12 import (
    assert_runner_writable_observer_dir_blocks,
    assert_strict_real_a2,
)
from witnessd.a2 import run_observer_launched_shell_lane, sudo_runner_for_user
from witnessd.emitter import emit_supervised_lane
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.signing import gen_operator_keypair


def _fixture() -> dict:
    return build_reference_adapter_fixture(build_shell_invocation("w12-real-a2"))


class TestW12PhaseAGates(unittest.TestCase):
    def test_sudo_runner_uses_noninteractive_runner_uid_boundary_command(self):
        with tempfile.TemporaryDirectory() as sandbox:
            with patch(
                "witnessd.a2.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                ),
            ) as run:
                receipt = sudo_runner_for_user("ubuntu")(["/usr/bin/true"], sandbox)

        run.assert_called_once_with(
            ["sudo", "-n", "-u", "ubuntu", "--", "/usr/bin/true"],
            cwd=sandbox,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            receipt,
            {
                "command": ["/usr/bin/true"],
                "exit_code": 0,
                "stdout": "ok\n",
                "stderr": "",
            },
        )

    def test_revalidate_w12_requires_real_phase_b_fixture_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-manifest.json"
            with patch("sys.stderr"):
                self.assertEqual(
                    revalidate_w12.main(["--manifest", str(missing)]),
                    2,
                )


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestW12RealA2(unittest.TestCase):
    def test_observer_launched_shell_lane_can_emit_strict_a2_with_fake_uid_boundary(self):
        runner_uid = 1001
        observer_uid = 2002

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox = root / "sandbox"
            evidence = root / "evidence"
            observer = root / "observer"
            keys = root / "keys"
            sandbox.mkdir()
            observer.mkdir()
            keys.mkdir()
            private_key, public_key = gen_operator_keypair(str(keys))

            def fake_runner(command: list[str], sandbox_path: str) -> dict:
                self.assertEqual(command, ["sh", "-c", "printf w12 > w12.txt"])
                Path(sandbox_path, "w12.txt").write_text("w12", encoding="utf-8")
                return {
                    "command": list(command),
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                }

            with patch(
                "witnessd.isolation.probe_lane_isolation",
                return_value={
                    "model": UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
                    "runner_uid": runner_uid,
                    "observer_uid": observer_uid,
                    "observer_dir_mode": "0700",
                    "observer_dir_writable_by_runner": False,
                    "observer_launched": True,
                },
            ):
                result = run_observer_launched_shell_lane(
                    sandbox=str(sandbox),
                    commands=[["sh", "-c", "printf w12 > w12.txt"]],
                    evidence_dir=str(evidence),
                    private_key_path=private_key,
                    public_key_path=public_key,
                    observer_dir=str(observer),
                    runner_user="ubuntu",
                    runner_uid=runner_uid,
                    allowed_touched_files=["w12.txt"],
                    command_runner=fake_runner,
                )

        manifest = result["manifest"]
        self.assertEqual(validate_capture_manifest(manifest), [])
        self.assertEqual(manifest["assurance"], "A2-isolated-observed")
        self.assertEqual(
            manifest["isolation"]["model"], UID_OBSERVER_LAUNCHED_ISOLATION_MODEL
        )
        self.assertEqual(manifest["isolation"]["observer_dir_mode"], "0700")
        self.assertIs(manifest["isolation"]["observer_launched"], True)
        assert_strict_real_a2(manifest)
        assert_runner_writable_observer_dir_blocks(manifest)

    def test_observer_launched_model_downgrades_without_launch_receipt(self):
        runner_uid = 1001
        observer_uid = 2002
        lane_result = {
            "command_receipts": [
                {
                    "command": ["sh", "-c", "true"],
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                }
            ],
            "touched_files": [],
            "test_output": {"status": "not-run"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            observer = root / "observer"
            keys = root / "keys"
            sandbox = root / "sandbox"
            observer.mkdir()
            keys.mkdir()
            sandbox.mkdir()
            private_key, public_key = gen_operator_keypair(str(keys))
            with patch(
                "witnessd.isolation.probe_lane_isolation",
                return_value={
                    "model": UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
                    "runner_uid": runner_uid,
                    "observer_uid": observer_uid,
                    "observer_dir_mode": "0700",
                    "observer_dir_writable_by_runner": False,
                    "observer_launched": False,
                },
            ):
                result = emit_supervised_lane(
                    lane_result,
                    str(evidence),
                    private_key,
                    fixture=_fixture(),
                    allowed_touched_files=[],
                    public_key_path=public_key,
                    observer_dir=str(observer),
                    runner_uid=runner_uid,
                    runner_sandbox=str(sandbox),
                    isolation_model=UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
                    observer_launched=False,
                )

        self.assertEqual(validate_capture_manifest(result["manifest"]), [])
        self.assertEqual(result["manifest"]["assurance"], "A1-local-observed")
        self.assertNotIn("isolation_hash", result["manifest"])


if __name__ == "__main__":
    unittest.main()
