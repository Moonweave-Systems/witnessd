import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestW13Revalidate(unittest.TestCase):
    def test_revalidate_w13_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "revalidate_w13.py")],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("revalidate_w13: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
