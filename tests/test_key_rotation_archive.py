import subprocess
import sys
import unittest


class TestKeyRotationArchive(unittest.TestCase):
    def test_archive_revalidates(self):
        result = subprocess.run(
            [sys.executable, "scripts/revalidate_key_rotation.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("key rotation revalidate: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
