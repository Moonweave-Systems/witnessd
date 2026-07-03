import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_verify.py"


class TestPilotVerifyTranscript(unittest.TestCase):
    def test_all_passed_true_only_from_zero_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "transcript.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--deployment-id",
                    "pilot-test",
                    "--out",
                    str(out_path),
                    "--production-command",
                    f"{sys.executable} -c \"print('production ok')\"",
                    "--canary-command",
                    f"{sys.executable} -c \"print('canary ok')\"",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            transcript = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(transcript["kind"], "depone-verification-transcript")
            self.assertTrue(transcript["all_passed"])
            self.assertEqual(
                [item["exit_code"] for item in transcript["results"]], [0, 0]
            )
            self.assertIn("production ok", transcript["results"][0]["stdout"])
            self.assertIn("canary ok", transcript["results"][1]["stdout"])

    def test_nonzero_real_exit_code_makes_all_passed_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "transcript.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--deployment-id",
                    "pilot-test",
                    "--out",
                    str(out_path),
                    "--production-command",
                    f"{sys.executable} -c \"print('production ok')\"",
                    "--canary-command",
                    f"{sys.executable} -c \"import sys; print('canary fail'); sys.exit(7)\"",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            transcript = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertFalse(transcript["all_passed"])
            self.assertEqual(
                [item["exit_code"] for item in transcript["results"]], [0, 7]
            )
            self.assertIn("canary fail", transcript["results"][1]["stdout"])


if __name__ == "__main__":
    unittest.main()
