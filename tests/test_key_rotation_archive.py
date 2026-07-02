import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.revalidate_key_rotation import (
    ARCHIVE,
    _load,
    _validate_canary_bundle_record,
    validate_archive,
)
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")


class TestKeyRotationArchive(unittest.TestCase):
    def test_archive_revalidates(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "revalidate_key_rotation.py")],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("key rotation revalidate: PASS", result.stdout)

    def test_archive_revalidates_from_non_repo_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ)
            depone_path = os.environ.get(
                "WITNESSD_DEPONE_ROOT", "/home/ubuntu/moonweave/depone"
            )
            env["PYTHONPATH"] = (
                depone_path
                if not env.get("PYTHONPATH")
                else depone_path + os.pathsep + env["PYTHONPATH"]
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "revalidate_key_rotation.py")],
                capture_output=True,
                text=True,
                check=False,
                cwd=d,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_current_archive_key_matches_runtime_default_and_canary(self):
        archive = _load(ARCHIVE)
        validate_archive(archive)
        current = [key for key in archive["keys"] if key["status"] == "current"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["key_id"], DEFAULT_OPERATOR_KEY_ID)
        self.assertEqual(
            current[0]["bundle_path"],
            "fixtures/key-rotation/operator-key-canary-bundle.json",
        )
        self.assertTrue(current[0]["canary"])

    def test_archive_rejects_backdated_rotation_metadata(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        mutated["keys"][0]["valid_until"] = "2030-01-01T00:00:00Z"
        with self.assertRaises(AssertionError):
            validate_archive(mutated)

    def test_production_gate_names_rollout_stage_and_required_evidence(self):
        archive = _load(ARCHIVE)
        validate_archive(archive)

        gate = archive["production_gate"]
        self.assertEqual(gate["rollout_stage"], "external-team-pilot")
        self.assertGreaterEqual(gate["deployments_min"], 1)
        self.assertEqual(
            [item["id"] for item in gate["required_evidence"]],
            [
                "deployment_record",
                "rotated_key_archive",
                "canary_bundle",
                "depone_verification",
                "operator_review",
            ],
        )
        self.assertTrue(all(item["status"] == "missing" for item in gate["required_evidence"]))

    def test_production_gate_cannot_open_without_required_evidence(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        mutated["production_gate"]["status"] = "open"
        with self.assertRaisesRegex(AssertionError, "deployment evidence"):
            validate_archive(mutated)

    def test_recorded_production_gate_evidence_must_match_artifact_hash(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        item = mutated["production_gate"]["required_evidence"][0]
        item["status"] = "recorded"
        item["artifact_path"] = "fixtures/key-rotation/operator-key-archive.json"
        item["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "artifact_sha256"):
            validate_archive(mutated)

    def test_production_gate_rejects_arbitrary_hash_bound_evidence_files(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        artifact_path = "SPEC.md"
        digest = _sha256(ROOT / artifact_path)
        mutated["production_gate"]["status"] = "open"
        for item in mutated["production_gate"]["required_evidence"]:
            item["status"] = "recorded"
            item["artifact_path"] = artifact_path
            item["artifact_sha256"] = digest
        with self.assertRaisesRegex(AssertionError, "wrong kind|duplicate artifact_path"):
            validate_archive(mutated)

    def test_production_gate_rejects_duplicate_recorded_artifact_paths(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            artifact_path = Path(d).relative_to(ROOT) / "deployment.json"
            _write_json(
                ROOT / artifact_path,
                {
                    "kind": "witnessd-external-team-pilot-deployment",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "operator": "operator@example.invalid",
                    "team_scope": "external-team:pilot",
                    "started_at": "2026-07-02T05:00:00Z",
                    "ended_at": "2026-07-02T05:30:00Z",
                    "witnessd_git_sha": "79e84b5",
                    "deployed_runtime": True,
                    "local_dogfood": False,
                    "ci_only": False,
                },
            )
            digest = _sha256(ROOT / artifact_path)
            mutated["production_gate"]["status"] = "open"
            for item in mutated["production_gate"]["required_evidence"]:
                item["status"] = "recorded"
                item["artifact_path"] = str(artifact_path)
                item["artifact_sha256"] = digest
            with self.assertRaisesRegex(AssertionError, "duplicate artifact_path"):
                validate_archive(mutated)

    def test_production_gate_rejects_fake_canary_bundle_path_not_linked_to_rotation(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            base = Path(d)
            rel_base = base.relative_to(ROOT)
            artifacts = {
                "deployment_record": rel_base / "deployment.json",
                "rotated_key_archive": rel_base / "rotation.json",
                "canary_bundle": rel_base / "fake-canary.json",
                "depone_verification": rel_base / "depone-verification.json",
                "operator_review": rel_base / "operator-review.json",
            }
            _write_json(
                ROOT / artifacts["deployment_record"],
                {
                    "kind": "witnessd-external-team-pilot-deployment",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "operator": "operator@example.invalid",
                    "team_scope": "external-team:pilot",
                    "started_at": "2026-07-02T05:00:00Z",
                    "ended_at": "2026-07-02T05:30:00Z",
                    "witnessd_git_sha": "79e84b5",
                    "deployed_runtime": True,
                    "local_dogfood": False,
                    "ci_only": False,
                },
            )
            _write_json(
                ROOT / artifacts["rotated_key_archive"],
                {
                    "kind": "witnessd-operator-key-rotation-record",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "retired_key_id": "witnessd-operator",
                    "current_key_id": DEFAULT_OPERATOR_KEY_ID,
                    "rotated_to": DEFAULT_OPERATOR_KEY_ID,
                    "canary_bundle_path": "fixtures/key-rotation/operator-key-canary-bundle.json",
                },
            )
            fake_canary = _load(ROOT / "fixtures/key-rotation/operator-key-canary-bundle.json")
            fake_canary["dsse_envelope"]["signatures"][0]["sig"] = "not-a-valid-signature"
            _write_json(ROOT / artifacts["canary_bundle"], fake_canary)
            _write_json(
                ROOT / artifacts["depone_verification"],
                {
                    "kind": "depone-verification-transcript",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "verifier": "depone",
                    "all_passed": True,
                    "results": [
                        {"name": "production_bundle", "exit_code": 0},
                        {"name": "canary_bundle", "exit_code": 0},
                    ],
                },
            )
            _write_json(
                ROOT / artifacts["operator_review"],
                {
                    "kind": "witnessd-operator-review",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "reviewer": "operator@example.invalid",
                    "reviewed_at": "2026-07-02T06:00:00Z",
                    "decision": "approve-keyless-gate",
                    "local_dogfood": False,
                    "private_keys_committed": False,
                    "private_keys_exposed": False,
                },
            )

            mutated["production_gate"]["status"] = "open"
            for item in mutated["production_gate"]["required_evidence"]:
                item["status"] = "recorded"
                artifact_path = artifacts[item["id"]]
                item["artifact_path"] = str(artifact_path)
                item["artifact_sha256"] = _sha256(ROOT / artifact_path)

            with self.assertRaisesRegex(AssertionError, "canary_bundle artifact_path"):
                validate_archive(mutated)

    def test_canary_bundle_record_rejects_invalid_signature(self):
        archive = _load(ARCHIVE)
        current_key = [key for key in archive["keys"] if key["status"] == "current"][0]
        fake_canary = _load(ROOT / current_key["bundle_path"])
        fake_canary["dsse_envelope"]["signatures"][0]["sig"] = "not-a-valid-signature"

        with self.assertRaisesRegex(AssertionError, "canary_bundle signature verification"):
            _validate_canary_bundle_record(
                fake_canary,
                artifact_path=(ROOT / current_key["bundle_path"]).resolve(),
                current_key=current_key,
            )

    def test_production_gate_can_open_with_semantic_hash_bound_required_evidence(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            base = Path(d)
            rel_base = base.relative_to(ROOT)
            artifacts = {
                "deployment_record": rel_base / "deployment.json",
                "rotated_key_archive": rel_base / "rotation.json",
                "depone_verification": rel_base / "depone-verification.json",
                "operator_review": rel_base / "operator-review.json",
            }
            _write_json(
                ROOT / artifacts["deployment_record"],
                {
                    "kind": "witnessd-external-team-pilot-deployment",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "operator": "operator@example.invalid",
                    "team_scope": "external-team:pilot",
                    "started_at": "2026-07-02T05:00:00Z",
                    "ended_at": "2026-07-02T05:30:00Z",
                    "witnessd_git_sha": "79e84b5",
                    "deployed_runtime": True,
                    "local_dogfood": False,
                    "ci_only": False,
                },
            )
            _write_json(
                ROOT / artifacts["rotated_key_archive"],
                {
                    "kind": "witnessd-operator-key-rotation-record",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "retired_key_id": "witnessd-operator",
                    "current_key_id": DEFAULT_OPERATOR_KEY_ID,
                    "rotated_to": DEFAULT_OPERATOR_KEY_ID,
                    "canary_bundle_path": "fixtures/key-rotation/operator-key-canary-bundle.json",
                },
            )
            _write_json(
                ROOT / artifacts["depone_verification"],
                {
                    "kind": "depone-verification-transcript",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "verifier": "depone",
                    "all_passed": True,
                    "results": [
                        {"name": "production_bundle", "exit_code": 0},
                        {"name": "canary_bundle", "exit_code": 0},
                    ],
                },
            )
            _write_json(
                ROOT / artifacts["operator_review"],
                {
                    "kind": "witnessd-operator-review",
                    "schema_version": "1.0",
                    "rollout_stage": "external-team-pilot",
                    "deployment_id": "pilot-2026-07-02",
                    "reviewer": "operator@example.invalid",
                    "reviewed_at": "2026-07-02T06:00:00Z",
                    "decision": "approve-keyless-gate",
                    "local_dogfood": False,
                    "private_keys_committed": False,
                    "private_keys_exposed": False,
                },
            )

            mutated["production_gate"]["status"] = "open"
            for item in mutated["production_gate"]["required_evidence"]:
                item["status"] = "recorded"
                if item["id"] == "canary_bundle":
                    artifact_path = Path("fixtures/key-rotation/operator-key-canary-bundle.json")
                else:
                    artifact_path = artifacts[item["id"]]
                item["artifact_path"] = str(artifact_path)
                item["artifact_sha256"] = _sha256(ROOT / artifact_path)

            validate_archive(mutated)


if __name__ == "__main__":
    unittest.main()
