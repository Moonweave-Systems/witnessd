import io
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.pilot import write_rotation_record
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID, gen_operator_keypair, verify_dsse

ROOT = Path(__file__).resolve().parents[1]


class TestPilotInit(unittest.TestCase):
    def test_init_defaults_to_local_dogfood_and_ci_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "pilot",
                        "init",
                        "--operator",
                        "operator@example.invalid",
                        "--team-scope",
                        "external-team:alpha",
                        "--out",
                        tmp,
                    ]
                )

            self.assertEqual(code, 0)
            record_path = Path(tmp) / "deployment-record.json"
            self.assertIn(str(record_path), out.getvalue())
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["kind"], "witnessd-external-team-pilot-deployment")
            self.assertEqual(record["schema_version"], "1.0")
            self.assertEqual(record["rollout_stage"], "external-team-pilot")
            self.assertTrue(record["deployment_id"].startswith("pilot-"))
            self.assertEqual(record["operator"], "operator@example.invalid")
            self.assertEqual(record["team_scope"], "external-team:alpha")
            self.assertIsNone(record["ended_at"])
            self.assertFalse(record["deployed_runtime"])
            self.assertTrue(record["local_dogfood"])
            self.assertTrue(record["ci_only"])
            self.assertRegex(record["witnessd_git_sha"], r"^[0-9a-f]{7,40}$")

    def test_init_requires_explicit_flags_to_claim_deployed_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "pilot",
                    "init",
                    "--operator",
                    "operator@example.invalid",
                    "--team-scope",
                    "external-team:alpha",
                    "--out",
                    tmp,
                    "--deployed-runtime",
                    "--not-dogfood",
                    "--not-ci",
                ]
            )

            self.assertEqual(code, 0)
            record = json.loads(
                (Path(tmp) / "deployment-record.json").read_text(encoding="utf-8")
            )
            self.assertTrue(record["deployed_runtime"])
            self.assertFalse(record["local_dogfood"])
            self.assertFalse(record["ci_only"])

    def test_init_records_deployment_root_git_sha(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            deploy = Path(tmp) / "deployed"
            deploy.mkdir()
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            }
            subprocess.run(["git", "init", "-q"], cwd=deploy, check=True)
            (deploy / "f.txt").write_text("x")
            subprocess.run(["git", "add", "-A"], cwd=deploy, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "seed"], cwd=deploy, env=env, check=True
            )
            deployed_sha = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=deploy,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            out = Path(tmp) / "out"
            code = main(
                [
                    "pilot",
                    "init",
                    "--operator",
                    "op",
                    "--team-scope",
                    "s",
                    "--out",
                    str(out),
                    "--deployment-root",
                    str(deploy),
                ]
            )
            self.assertEqual(code, 0)
            record = json.loads(
                (out / "deployment-record.json").read_text(encoding="utf-8")
            )
            # Records the DEPLOYED runtime's SHA, not this dev tree's HEAD.
            self.assertEqual(record["witnessd_git_sha"], deployed_sha)


class TestPilotClose(unittest.TestCase):
    def test_close_fills_end_time_and_prints_record_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "pilot",
                    "init",
                    "--operator",
                    "operator@example.invalid",
                    "--team-scope",
                    "external-team:alpha",
                    "--out",
                    tmp,
                ]
            )
            self.assertEqual(code, 0)
            record_path = Path(tmp) / "deployment-record.json"

            out = io.StringIO()
            with redirect_stdout(out):
                close_code = main(["pilot", "close", "--record", str(record_path)])

            self.assertEqual(close_code, 0)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(record["ended_at"])
            self.assertGreaterEqual(record["ended_at"], record["started_at"])
            digest = hashlib.sha256(record_path.read_bytes()).hexdigest()
            self.assertIn(digest, out.getvalue())

    def test_close_preserves_existing_end_time_for_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "deployment-record.json"
            record_path.write_text(
                json.dumps(
                    {
                        "kind": "witnessd-external-team-pilot-deployment",
                        "schema_version": "1.0",
                        "rollout_stage": "external-team-pilot",
                        "deployment_id": "pilot-test",
                        "operator": "operator@example.invalid",
                        "team_scope": "external-team:alpha",
                        "started_at": "2026-07-03T00:00:00Z",
                        "ended_at": "2026-07-03T00:05:00Z",
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
            before = record_path.read_text(encoding="utf-8")

            close_code = main(["pilot", "close", "--record", str(record_path)])

            self.assertEqual(close_code, 0)
            self.assertEqual(record_path.read_text(encoding="utf-8"), before)


class TestPilotRotationRecord(unittest.TestCase):
    def test_rotation_record_matches_current_archive_canary_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = ROOT / "fixtures/key-rotation/operator-key-archive.json"

            record_path = write_rotation_record(
                archive_path=archive_path,
                out_dir=tmp,
                retired_key_id="witnessd-operator",
            )

            record = json.loads(record_path.read_text(encoding="utf-8"))
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
            current = [
                key for key in archive["keys"] if key.get("status") == "current"
            ][0]
            self.assertEqual(
                record,
                {
                    "canary_bundle_path": current["bundle_path"],
                    "current_key_id": DEFAULT_OPERATOR_KEY_ID,
                    "kind": "witnessd-operator-key-rotation-record",
                    "retired_key_id": "witnessd-operator",
                    "rollout_stage": "external-team-pilot",
                    "rotated_to": DEFAULT_OPERATOR_KEY_ID,
                    "schema_version": "1.0",
                },
            )
            self.assertNotEqual(record["retired_key_id"], record["current_key_id"])
            self.assertEqual(record["current_key_id"], record["rotated_to"])
            self.assertEqual(record["canary_bundle_path"], current["bundle_path"])

    def test_rotation_record_cli_writes_record_and_prints_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "pilot",
                        "rotation-record",
                        "--archive",
                        "fixtures/key-rotation/operator-key-archive.json",
                        "--out",
                        tmp,
                    ]
                )

            self.assertEqual(code, 0)
            record_path = Path(tmp) / "rotation-record.json"
            self.assertIn(str(record_path), out.getvalue())
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["retired_key_id"], "witnessd-operator")
            self.assertEqual(record["current_key_id"], DEFAULT_OPERATOR_KEY_ID)


@unittest.skipUnless(shutil.which("openssl"), "openssl required to sign canary bundle")
class TestPilotCanary(unittest.TestCase):
    def test_canary_emits_single_signature_operator_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / "keys"
            out_dir = Path(tmp) / "canary"
            keys_dir.mkdir()
            _private_key, public_key = gen_operator_keypair(str(keys_dir))

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "pilot",
                        "canary",
                        "--keys-dir",
                        str(keys_dir),
                        "--out",
                        str(out_dir),
                    ]
                )

            self.assertEqual(code, 0)
            bundle_path = out_dir / "operator-key-canary-bundle.json"
            self.assertIn(str(bundle_path), out.getvalue())
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(bundle["kind"], "depone-evidence-substrate-bundle")
            self.assertEqual(
                bundle["statement"]["predicate"]["source_kind"],
                "operator-key-rotation-canary",
            )
            signatures = bundle["dsse_envelope"]["signatures"]
            self.assertEqual(len(signatures), 1)
            self.assertRegex(signatures[0]["keyid"], r"^sha256:[0-9a-f]{64}$")
            self.assertTrue(verify_dsse(bundle["dsse_envelope"], public_key))


class TestPilotArchiveEvidence(unittest.TestCase):
    def test_archive_evidence_records_supplied_item_without_opening_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "operator-key-archive.json"
            archive = json.loads(
                (ROOT / "fixtures/key-rotation/operator-key-archive.json").read_text(
                    encoding="utf-8"
                )
            )
            # Seed a blocked, all-missing gate so this exercises the invariant
            # (archive-evidence records an item without opening the gate)
            # independent of the committed archive's live open/blocked state.
            archive["production_gate"]["status"] = "blocked"
            for item in archive["production_gate"]["required_evidence"]:
                item["status"] = "missing"
                item.pop("artifact_path", None)
                item.pop("artifact_sha256", None)
            archive_path.write_text(
                json.dumps(archive, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            artifact_path = Path(tmp) / "deployment-record.json"
            artifact_path.write_text('{"kind":"evidence"}\n', encoding="utf-8")
            out_path = Path(tmp) / "updated-archive.json"

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "pilot",
                        "archive-evidence",
                        "--archive",
                        str(archive_path),
                        "--out",
                        str(out_path),
                        "--artifact",
                        f"deployment_record={artifact_path}",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn(str(out_path), out.getvalue())
            updated = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["production_gate"]["status"], "blocked")
            item = updated["production_gate"]["required_evidence"][0]
            self.assertEqual(item["id"], "deployment_record")
            self.assertEqual(item["status"], "recorded")
            self.assertEqual(item["artifact_path"], str(artifact_path))
            self.assertEqual(
                item["artifact_sha256"],
                hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            )
            for before, after in zip(
                archive["production_gate"]["required_evidence"][1:],
                updated["production_gate"]["required_evidence"][1:],
            ):
                self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
