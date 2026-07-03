import io
import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID, gen_operator_keypair, verify_dsse


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
            self.assertEqual(
                record["kind"], "witnessd-external-team-pilot-deployment"
            )
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
            self.assertEqual(signatures[0]["keyid"], DEFAULT_OPERATOR_KEY_ID)
            self.assertTrue(verify_dsse(bundle["dsse_envelope"], public_key))


if __name__ == "__main__":
    unittest.main()
