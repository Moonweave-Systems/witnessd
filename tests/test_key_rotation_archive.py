import copy
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.revalidate_key_rotation import ARCHIVE, _load, validate_archive
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID

ROOT = Path(__file__).resolve().parents[1]


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
            depone_path = "/home/ubuntu/moonweave/depone"
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

    def test_production_gate_can_open_with_hash_bound_required_evidence(self):
        archive = _load(ARCHIVE)
        mutated = copy.deepcopy(archive)
        artifact_path = "fixtures/key-rotation/operator-key-archive.json"
        digest = hashlib.sha256((ROOT / artifact_path).read_bytes()).hexdigest()
        mutated["production_gate"]["status"] = "open"
        for item in mutated["production_gate"]["required_evidence"]:
            item["status"] = "recorded"
            item["artifact_path"] = artifact_path
            item["artifact_sha256"] = digest
        validate_archive(mutated)


if __name__ == "__main__":
    unittest.main()
