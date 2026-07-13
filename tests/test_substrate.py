import json
import os
import shutil
import tempfile
import unittest

from depone.agent_fabric.evidence_substrate import (
    DSSE_PAYLOAD_TYPE,
    ingest_signed_evidence_bundle,
)
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture
from depone.agent_fabric.sign import verify_signed_bundle
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.evidence_contract import validate_evidence_contract

from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.observer import build_observer_capture
from witnessd.runintent import build_run_intent, write_signed_run_intent
from witnessd.signing import gen_operator_keypair
from witnessd.substrate import build_bundle, build_evidence_contract


def _fixture() -> dict:
    invocation = {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": "w1-task10",
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


def _a1_manifest() -> dict:
    fixture = _fixture()
    observer_capture = build_observer_capture(
        command_receipts=[
            {"command": ["sh", "-c", "true"], "exit_code": 0, "status": "passed"}
        ],
        touched_files=["depone/example.py"],
        allowed_touched_files=["depone/example.py"],
        test_output={"status": "passed", "summary": "1 passed"},
    )
    return build_capture_manifest(
        fixture,
        observer_capture=observer_capture,
        allowed_touched_files=["depone/example.py"],
    )


def _write_artifacts(tmp: str, manifest: dict) -> dict[str, str]:
    manifest_path = os.path.join(tmp, "capture-manifest.json")
    observer_path = os.path.join(tmp, "observer-capture.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    with open(observer_path, "w", encoding="utf-8") as handle:
        json.dump(manifest["observer_capture"], handle)
    return {"capture-manifest": manifest_path, "observer-capture": observer_path}


def _add_run_intent_artifact(
    tmp: str, artifacts: dict[str, str], private_key_path: str
) -> None:
    intent = build_run_intent(
        run_id="w1-task10",
        baseline={"git_head": "test-head", "git_status_state": "known"},
        allowed_paths=["depone/example.py"],
        approval_policy="on-request",
        sandbox_mode="workspace-write",
        provider="test",
        instruction_hashes={"prompt_sha256": canonical_hash("test prompt")},
        budgets={"max_tokens": 1000, "max_usd": 1.0, "max_depth": 1},
        capture_profile="full",
    )
    path = os.path.join(tmp, "run-intent.json")
    write_signed_run_intent(path, intent, private_key_path, key_id="test-key")
    artifacts["run-intent"] = path


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestBundleSigned(unittest.TestCase):
    def test_signed_bundle_ingests_all_subjects(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _a1_manifest()
            artifacts = _write_artifacts(tmp, manifest)
            keydir = os.path.join(tmp, "keys")
            os.makedirs(keydir)
            priv, pub = gen_operator_keypair(keydir)
            _add_run_intent_artifact(tmp, artifacts, priv)

            bundle = build_bundle(manifest, artifacts, priv, pub)

            # in-toto Statement v1
            self.assertEqual(
                bundle["statement"]["_type"], "https://in-toto.io/Statement/v1"
            )
            # DSSE signed
            self.assertEqual(bundle["dsse_envelope"]["payloadType"], DSSE_PAYLOAD_TYPE)
            self.assertTrue(bundle["dsse_envelope"]["signatures"])
            predicate = bundle["statement"]["predicate"]
            self.assertEqual(
                [item["name"] for item in predicate["artifact_index"]],
                ["capture-manifest", "observer-capture", "run-intent"],
            )
            self.assertRegex(predicate["artifact_merkle_root"], r"^[0-9a-f]{64}$")
            # inline otel spans, no invented usage fields
            self.assertTrue(bundle["otel_spans"])
            usage_keys = [
                key
                for span in bundle["otel_spans"]
                for key in span.get("attributes", {})
                if key.startswith("gen_ai.usage.")
            ]
            self.assertEqual(usage_keys, [])

            self.assertTrue(verify_signed_bundle(bundle, pub))

            verdict = ingest_signed_evidence_bundle(
                bundle, pub, artifacts, otel_spans=bundle["otel_spans"]
            )
            self.assertTrue(verdict["signature_verified"])
            self.assertEqual(verdict["decision"], "pass")
            self.assertTrue(verdict["subject_results"])
            self.assertTrue(
                all(r["status"] == "verified" for r in verdict["subject_results"])
            )
            self.assertEqual(verdict.get("otel_errors"), [])

    def test_signed_bundle_blocks_after_artifact_byte_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _a1_manifest()
            artifacts = _write_artifacts(tmp, manifest)
            keydir = os.path.join(tmp, "keys")
            os.makedirs(keydir)
            priv, pub = gen_operator_keypair(keydir)
            _add_run_intent_artifact(tmp, artifacts, priv)
            bundle = build_bundle(manifest, artifacts, priv, pub)
            with open(artifacts["observer-capture"], "ab") as handle:
                handle.write(b"x")

            verdict = ingest_signed_evidence_bundle(
                bundle, pub, artifacts, otel_spans=bundle["otel_spans"]
            )

            self.assertEqual(verdict["decision"], "blocked")
            self.assertIn(
                "mismatch",
                {result["status"] for result in verdict["subject_results"]},
            )

    def test_assurance_not_upgraded_past_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _a1_manifest()
            artifacts = _write_artifacts(tmp, manifest)
            keydir = os.path.join(tmp, "keys")
            os.makedirs(keydir)
            priv, pub = gen_operator_keypair(keydir)
            _add_run_intent_artifact(tmp, artifacts, priv)
            bundle = build_bundle(manifest, artifacts, priv, pub)
            self.assertEqual(bundle["assurance"], manifest["assurance"])
            self.assertEqual(
                bundle["statement"]["predicate"]["assurance"], manifest["assurance"]
            )


class TestBundleUnsigned(unittest.TestCase):
    def test_unsigned_bundle_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _a1_manifest()
            artifacts = _write_artifacts(tmp, manifest)
            bundle = build_bundle(manifest, artifacts)
            self.assertEqual(bundle["dsse_envelope"]["signatures"], [])
            self.assertEqual(bundle["signing_status"], "unsigned-content-addressed")
            self.assertNotIn("signature_boundary", bundle)
            self.assertIs(bundle["boundary"]["signed"], False)
            # no assurance upgrade
            self.assertEqual(bundle["assurance"], manifest["assurance"])


class TestEvidenceContract(unittest.TestCase):
    def _context(self, files: dict[str, str]) -> EvidenceContext:
        evidence_files = [
            EvidenceFile(path=name, content=content, sha256=canonical_hash(content))
            for name, content in files.items()
        ]
        return EvidenceContext(run_id="w1-task10", files=evidence_files, raw={})

    def test_contract_has_enforcement_directive(self):
        files = build_evidence_contract(
            allowed_touched_files=["depone/example.py"],
            touched_files=["depone/example.py"],
            exit_code=0,
        )
        self.assertIn("evidence-contract.json", files)
        contract = json.loads(files["evidence-contract.json"])
        self.assertEqual(contract["schema_version"], "v105.verify_wedge")

        errors = validate_evidence_contract(self._context(files))
        self.assertEqual(errors, [])

    def test_write_scope_contract_uses_role_capability_schema(self):
        files = build_evidence_contract(
            allowed_touched_files=["depone/example.py"],
            touched_files=["depone/example.py"],
            exit_code=0,
            write_scope=["depone/**"],
        )

        contract = json.loads(files["evidence-contract.json"])
        self.assertEqual(
            contract["schema_version"], "v109.role_capability_write_scope"
        )
        self.assertEqual(
            contract["role_capability_write_scope"],
            {
                "run_intent_path": "run-intent.json",
                "bundle_path": "bundle.json",
            },
        )
        errors = validate_evidence_contract(self._context(files))
        self.assertTrue(
            any(e.code == "ERR_ROLE_CAPABILITY_RUN_INTENT_MISSING" for e in errors),
            errors,
        )

    def test_tool_call_contract_uses_v107_schema(self):
        files = build_evidence_contract(
            allowed_touched_files=["depone/example.py"],
            touched_files=["depone/example.py"],
            exit_code=0,
            write_scope=["depone/**"],
            tool_call_decision_receipts=True,
        )

        contract = json.loads(files["evidence-contract.json"])
        self.assertEqual(
            contract["schema_version"], "v107.role_capability_tool_calls"
        )
        self.assertEqual(
            contract["role_capability_write_scope"],
            {
                "run_intent_path": "run-intent.json",
                "bundle_path": "bundle.json",
            },
        )
        self.assertEqual(
            contract["role_capability_tool_calls"],
            {
                "run_intent_path": "run-intent.json",
                "bundle_path": "bundle.json",
                "decision_receipts_path": "tool-call-decision-receipts.json",
            },
        )
        errors = validate_evidence_contract(self._context(files))
        self.assertTrue(
            any(e.code == "ERR_ROLE_CAPABILITY_RUN_INTENT_MISSING" for e in errors),
            errors,
        )

    def test_forbidden_touched_file_detected(self):
        files = build_evidence_contract(
            allowed_touched_files=["depone/example.py"],
            touched_files=["depone/example.py", "secrets.env"],
            exit_code=0,
        )
        errors = validate_evidence_contract(self._context(files))
        self.assertTrue(any(e.code == "ERR_FORBIDDEN_FILE_TOUCHED" for e in errors))


if __name__ == "__main__":
    unittest.main()
