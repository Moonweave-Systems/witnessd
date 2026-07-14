import subprocess
import tempfile
import unittest

from witnessd.codex_capability import validate_codex_local_capability
from witnessd.preflight import PreflightError, probe_adapter_capability


class TestPreflight(unittest.TestCase):
    def test_codex_capability_receipt_valid_and_blocked_when_missing(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)

            cap = probe_adapter_capability(
                "codex",
                repo=repo,
                codex_binary="definitely-not-a-real-binary",
            )

            self.assertEqual(validate_codex_local_capability(cap), [])
            self.assertEqual(cap["decision"], "blocked")
            self.assertTrue(cap["blocked_reasons"])
            self.assertIs(cap["boundary"]["launches_live_model"], False)
            self.assertIs(cap["boundary"]["executes_coding_task"], False)
            self.assertIs(cap["boundary"]["captures_capability_only"], True)
            self.assertIs(cap["boundary"]["raises_assurance"], False)

    def test_require_ready_raises_when_blocked(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)

            with self.assertRaises(PreflightError) as cm:
                probe_adapter_capability(
                    "codex",
                    repo=repo,
                    codex_binary="definitely-not-a-real-binary",
                    require_ready=True,
                )

            self.assertEqual(
                cm.exception.code, "ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE"
            )


if __name__ == "__main__":
    unittest.main()
