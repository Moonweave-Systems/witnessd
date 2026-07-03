import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_pilot_gate_evidence import main

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestBuildPilotGateEvidence(unittest.TestCase):
    def test_driver_closes_copies_and_archives_four_operator_artifacts_idempotently(
        self,
    ):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            pilot_root = base / "pilot"
            deployment_dir = pilot_root / "deployment"
            deployment_dir.mkdir(parents=True)
            (pilot_root / "production-ev").mkdir()
            deployment_record = deployment_dir / "deployment-record.json"
            deployment_record.write_text(
                json.dumps(
                    {
                        "kind": "witnessd-external-team-pilot-deployment",
                        "schema_version": "1.0",
                        "rollout_stage": "external-team-pilot",
                        "deployment_id": "pilot-test",
                        "operator": "operator@example.invalid",
                        "team_scope": "external-team:alpha",
                        "started_at": "2026-07-03T00:00:00Z",
                        "ended_at": None,
                        "witnessd_git_sha": "9e206a32ddd9",
                        "deployed_runtime": True,
                        "local_dogfood": False,
                        "ci_only": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            archive = base / "operator-key-archive.json"
            # Seed a blocked, all-missing gate so the driver is exercised against
            # a closed gate regardless of the committed archive's live state; the
            # invariant under test is that the driver records evidence without
            # opening the gate.
            seed_archive = json.loads(
                (ROOT / "fixtures/key-rotation/operator-key-archive.json").read_text(
                    encoding="utf-8"
                )
            )
            seed_archive["production_gate"]["status"] = "blocked"
            for item in seed_archive["production_gate"]["required_evidence"]:
                item["status"] = "missing"
                item.pop("artifact_path", None)
                item.pop("artifact_sha256", None)
            archive.write_text(
                json.dumps(seed_archive, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            stable_dir = base / "external-team-pilot"
            production_command = f"{sys.executable} -c \"print('production ok')\""
            canary_command = f"{sys.executable} -c \"print('canary ok')\""
            args = [
                "--pilot-root",
                str(pilot_root),
                "--archive",
                str(archive),
                "--stable-dir",
                str(stable_dir),
                "--production-command",
                production_command,
                "--canary-command",
                canary_command,
                "--verify-cwd",
                str(ROOT),
            ]

            self.assertEqual(main(args), 0)
            first_archive = archive.read_text(encoding="utf-8")
            first_deployment = (stable_dir / "deployment-record.json").read_text(
                encoding="utf-8"
            )
            first_transcript = (
                stable_dir / "depone-verification-transcript.json"
            ).read_text(encoding="utf-8")

            self.assertEqual(main(args), 0)

            self.assertEqual(archive.read_text(encoding="utf-8"), first_archive)
            self.assertEqual(
                (stable_dir / "deployment-record.json").read_text(encoding="utf-8"),
                first_deployment,
            )
            self.assertEqual(
                (stable_dir / "depone-verification-transcript.json").read_text(
                    encoding="utf-8"
                ),
                first_transcript,
            )
            copied_deployment = json.loads(first_deployment)
            self.assertIsNotNone(copied_deployment["ended_at"])
            rotation = json.loads(
                (stable_dir / "rotation-record.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                rotation["canary_bundle_path"],
                "fixtures/key-rotation/operator-key-canary-bundle.json",
            )
            transcript = json.loads(first_transcript)
            self.assertTrue(transcript["all_passed"])
            self.assertEqual(
                [item["exit_code"] for item in transcript["results"]], [0, 0]
            )

            updated_archive = json.loads(first_archive)
            self.assertEqual(updated_archive["production_gate"]["status"], "blocked")
            evidence = {
                item["id"]: item
                for item in updated_archive["production_gate"]["required_evidence"]
            }
            expected_paths = {
                "deployment_record": stable_dir / "deployment-record.json",
                "rotated_key_archive": stable_dir / "rotation-record.json",
                "canary_bundle": ROOT
                / "fixtures/key-rotation/operator-key-canary-bundle.json",
                "depone_verification": stable_dir
                / "depone-verification-transcript.json",
            }
            for evidence_id, path in expected_paths.items():
                item = evidence[evidence_id]
                self.assertEqual(item["artifact_path"], str(path.relative_to(ROOT)))
                self.assertEqual(item["artifact_sha256"], _sha256(path))
            self.assertNotIn("artifact_path", evidence["operator_review"])


if __name__ == "__main__":
    unittest.main()
