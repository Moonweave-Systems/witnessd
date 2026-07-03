import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


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


if __name__ == "__main__":
    unittest.main()
