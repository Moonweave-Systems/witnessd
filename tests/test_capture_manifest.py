import unittest
from copy import deepcopy

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture

from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.observer import build_observer_capture


def _fixture() -> dict:
    invocation = {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": "w1-task7",
        "role": "runner",
        "toolbelt": {
            "allowed_tools": ["cat", "python3"],
            "allowed_mcp": [],
            "forbidden_tools": ["write"],
            "context_policy": "local-code-only",
            "output_schema": "runner-result-v1",
            "evidence_obligations": ["command_receipt"],
        },
        "instructions": "Run checks and report outputs.",
        "evidence_obligations": ["command_receipt"],
        "context_policy": "local-code-only",
    }
    return build_reference_adapter_fixture(invocation)


def _make_a1_manifest(prev: str | None = None) -> dict:
    fixture = _fixture()
    observer_capture = build_observer_capture(
        command_receipts=[{"command": ["sh", "-c", "true"], "exit_code": 0}],
        touched_files=["depone/example.py"],
        allowed_touched_files=["depone/example.py"],
        test_output={"status": "passed", "summary": "1 passed"},
    )
    return build_capture_manifest(
        fixture,
        observer_capture=observer_capture,
        allowed_touched_files=["depone/example.py"],
        prev_capture_hash=prev,
    )


class TestManifest(unittest.TestCase):
    def test_a1_manifest_valid(self):
        manifest = _make_a1_manifest()
        self.assertEqual(validate_capture_manifest(manifest), [])
        self.assertEqual(manifest["assurance"], "A1-local-observed")
        self.assertEqual(manifest["decision"], "observed-local-capture")
        self.assertIsNone(manifest["prev_capture_hash"])

    def test_chain_links_pass(self):
        m1 = _make_a1_manifest(prev=None)
        m2 = _make_a1_manifest(prev=canonical_hash(m1))
        result = verify_capture_chain([m1, m2])
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(m2["prev_capture_hash"], canonical_hash(m1))

    def test_reordered_chain_blocked(self):
        m1 = _make_a1_manifest(prev=None)
        m2 = _make_a1_manifest(prev=canonical_hash(m1))
        result = verify_capture_chain([m2, m1])
        self.assertEqual(result["decision"], "blocked")

    def test_observer_capture_hash_binds(self):
        manifest = _make_a1_manifest()
        tampered = deepcopy(manifest)
        tampered["observer_capture"]["test_output"]["summary"] = "tampered"
        self.assertTrue(
            any(
                "observer_capture_hash mismatch" in error
                for error in validate_capture_manifest(tampered)
            )
        )

    def test_a0_manifest_without_observer(self):
        manifest = build_capture_manifest(_fixture())
        self.assertEqual(manifest["assurance"], "A0-claims-only")
        self.assertEqual(validate_capture_manifest(manifest), [])


if __name__ == "__main__":
    unittest.main()
