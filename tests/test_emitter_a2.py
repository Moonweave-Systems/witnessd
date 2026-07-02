import os
import shutil
import stat
import tempfile
import unittest

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture

from witnessd.adapters.shell import run_shell_lane
from witnessd.emitter import emit_supervised_lane


def _fixture() -> dict:
    return build_reference_adapter_fixture(
        {
            "packet_version": "1.0",
            "target_harness": "shell",
            "profile": "w2-supervised",
            "role": "runner",
            "toolbelt": {
                "allowed_tools": ["sh"],
                "allowed_mcp": [],
                "forbidden_tools": ["write"],
                "context_policy": "local-code-only",
                "output_schema": "runner-result-v1",
                "evidence_obligations": ["command_receipt"],
            },
            "instructions": "Run supervised lane.",
            "evidence_obligations": ["command_receipt"],
            "context_policy": "local-code-only",
        }
    )


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestEmitterA2(unittest.TestCase):
    def _lane(self, sandbox: str) -> dict:
        return run_shell_lane(
            sandbox=sandbox,
            commands=[["sh", "-c", "echo hi > f.txt"]],
            test_command=["sh", "-c", "true"],
        )

    def test_supervised_lane_emits_a2_when_uid_boundary_holds(self):
        from witnessd.signing import gen_operator_keypair

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = os.path.join(tmp, "sandbox")
            evidence_dir = os.path.join(tmp, "evidence")
            observer_dir = os.path.join(tmp, "observer")
            keys = os.path.join(tmp, "keys")
            os.makedirs(sandbox)
            os.makedirs(observer_dir)
            os.chmod(observer_dir, stat.S_IRWXU)
            os.makedirs(keys)
            priv, pub = gen_operator_keypair(keys)

            result = emit_supervised_lane(
                self._lane(sandbox),
                evidence_dir,
                priv,
                fixture=_fixture(),
                allowed_touched_files=["f.txt"],
                public_key_path=pub,
                observer_dir=observer_dir,
                runner_uid=os.getuid() + 1,
                task_id="w2-a2",
                runner_sandbox=sandbox,
            )

            manifest = result["manifest"]
            self.assertEqual(validate_capture_manifest(manifest), [])
            self.assertEqual(manifest["assurance"], "A2-isolated-observed")
            self.assertEqual(manifest["decision"], "isolated-observed")
            self.assertIn("isolation_hash", manifest)

    def test_supervised_lane_downgrades_same_uid_to_a1(self):
        from witnessd.signing import gen_operator_keypair

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = os.path.join(tmp, "sandbox")
            evidence_dir = os.path.join(tmp, "evidence")
            observer_dir = os.path.join(tmp, "observer")
            keys = os.path.join(tmp, "keys")
            os.makedirs(sandbox)
            os.makedirs(observer_dir)
            os.chmod(observer_dir, stat.S_IRWXU)
            os.makedirs(keys)
            priv, pub = gen_operator_keypair(keys)

            result = emit_supervised_lane(
                self._lane(sandbox),
                evidence_dir,
                priv,
                fixture=_fixture(),
                allowed_touched_files=["f.txt"],
                public_key_path=pub,
                observer_dir=observer_dir,
                runner_uid=os.getuid(),
                task_id="w2-a1",
                runner_sandbox=sandbox,
            )

            manifest = result["manifest"]
            self.assertEqual(validate_capture_manifest(manifest), [])
            self.assertEqual(manifest["assurance"], "A1-local-observed")
            self.assertNotIn("isolation_hash", manifest)


if __name__ == "__main__":
    unittest.main()
