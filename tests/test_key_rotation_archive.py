import copy
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


if __name__ == "__main__":
    unittest.main()
