import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestRevalidateV2Demo(unittest.TestCase):
    def test_revalidate_v2_demo_fixture_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "revalidate_v2_demo.py")],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("revalidate_v2_demo: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
