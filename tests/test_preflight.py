import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_opencode_binary_present_still_blocks_known_headless_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            opencode = root / "opencode"
            opencode.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            opencode.chmod(0o755)

            path = f"{root}{os.pathsep}{os.environ.get('PATH', '')}"
            with patch.dict(os.environ, {"PATH": path}):
                receipt = probe_adapter_capability(
                    "opencode",
                    repo=str(repo),
                )
                self.assertEqual(receipt["decision"], "blocked")
                self.assertIn(
                    "ERR_OPENCODE_HEADLESS_NOOP_KNOWN_NONFUNCTIONAL",
                    " ".join(receipt["blocked_reasons"]),
                )

                with self.assertRaises(PreflightError) as cm:
                    probe_adapter_capability(
                        "opencode",
                        repo=str(repo),
                        require_ready=True,
                    )
            self.assertEqual(
                cm.exception.code, "ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE"
            )
            self.assertIn(
                "ERR_OPENCODE_HEADLESS_NOOP_KNOWN_NONFUNCTIONAL",
                cm.exception.message,
            )


if __name__ == "__main__":
    unittest.main()
