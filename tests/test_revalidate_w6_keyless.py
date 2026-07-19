import subprocess
import sys
import unittest
from pathlib import Path

try:
    import cryptography  # noqa: F401

    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


ROOT = Path(__file__).resolve().parents[1]


class TestRevalidateW6Keyless(unittest.TestCase):
    @unittest.skipUnless(
        HAS_CRYPTOGRAPHY, "revalidator re-derives via Depone[keyless] (cryptography)"
    )
    def test_keyless_revalidates_open_gate_and_fail_closed_emission(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "revalidate_w6_keyless.py")],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("keyless emission readiness revalidate: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
