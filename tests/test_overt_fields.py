import json
import os
import shutil
import tempfile
import unittest

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture

from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.observer import build_observer_capture
from witnessd.signing import gen_operator_keypair
from witnessd.substrate import build_bundle


def _fixture() -> dict:
    invocation = {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": "w8-overt",
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


def _observer_capture() -> dict:
    return build_observer_capture(
        command_receipts=[
            {"command": ["sh", "-c", "true"], "exit_code": 0, "status": "passed"}
        ],
        touched_files=["depone/example.py"],
        allowed_touched_files=["depone/example.py"],
        test_output={"status": "passed", "summary": "1 passed"},
    )


def _manifest(**kwargs) -> dict:
    return build_capture_manifest(
        _fixture(),
        observer_capture=_observer_capture(),
        allowed_touched_files=["depone/example.py"],
        **kwargs,
    )


def _assert_reconstruction_self_declares_post_hoc(manifest: dict, *, reconstructed: bool) -> None:
    """Local fixture discipline only; no live notary proves temporality."""

    if reconstructed and manifest.get("evidence_mode") != "post_hoc":
        raise AssertionError("post-hoc reconstructed evidence must self-declare post_hoc")


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestOvertFields(unittest.TestCase):
    def test_capture_manifest_defaults_are_depone_additive(self):
        manifest = _manifest()

        self.assertEqual(manifest["evidence_mode"], "contemporaneous")
        self.assertEqual(manifest["epoch_seconds"], 300)
        self.assertEqual(manifest["monotonic_counter"], 1)
        self.assertNotIn("parent_attestation_id", manifest)
        self.assertEqual(validate_capture_manifest(manifest), [])

    def test_post_hoc_bundle_fields_survive_signed_depone_ingest(self):
        parent = canonical_hash({"upstream": "team-ledger"})
        manifest = _manifest(
            evidence_mode="post_hoc",
            epoch_seconds=300,
            monotonic_counter=7,
            parent_attestation_id=parent,
        )
        _assert_reconstruction_self_declares_post_hoc(manifest, reconstructed=True)

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "capture-manifest.json")
            observer_path = os.path.join(tmp, "observer-capture.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with open(observer_path, "w", encoding="utf-8") as handle:
                json.dump(manifest["observer_capture"], handle)

            keydir = os.path.join(tmp, "keys")
            os.makedirs(keydir)
            private_key, public_key = gen_operator_keypair(keydir)
            artifacts = {
                "capture-manifest": manifest_path,
                "observer-capture": observer_path,
            }
            bundle = build_bundle(manifest, artifacts, private_key, public_key)

            self.assertEqual(bundle["evidence_mode"], "post_hoc")
            self.assertEqual(bundle["epoch_seconds"], 300)
            self.assertEqual(bundle["monotonic_counter"], 7)
            self.assertEqual(bundle["parent_attestation_id"], parent)
            predicate = bundle["statement"]["predicate"]
            self.assertEqual(predicate["evidence_mode"], "post_hoc")
            self.assertEqual(predicate["parent_attestation_id"], parent)

            verdict = ingest_signed_evidence_bundle(
                bundle,
                public_key,
                artifacts,
                otel_spans=bundle["otel_spans"],
            )

        self.assertTrue(verdict["signature_verified"])
        self.assertEqual(verdict["decision"], "pass")

    def test_reconstructed_fixture_discipline_rejects_contemporaneous_label(self):
        manifest = _manifest(evidence_mode="contemporaneous")

        with self.assertRaisesRegex(AssertionError, "post_hoc"):
            _assert_reconstruction_self_declares_post_hoc(manifest, reconstructed=True)

    def test_parent_attestation_id_must_be_sha256_hex(self):
        with self.assertRaisesRegex(ValueError, "parent_attestation_id"):
            _manifest(parent_attestation_id="not-a-sha")


if __name__ == "__main__":
    unittest.main()
