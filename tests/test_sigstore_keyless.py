from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from witnessd.adapters.sigstore_keyless import attest_keyless


FIXTURES = Path(__file__).parent / "fixtures" / "sigstore-keyless"


class SigstoreKeylessAdapterTests(unittest.TestCase):
    def test_attest_returns_parsed_real_bundle_and_uses_safe_flags(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.whl"
            evidence.write_bytes(
                (FIXTURES / "evidence-sigstore-4.4.0.whl").read_bytes()
            )

            def fake_run(command, **kwargs):
                if "--version" in command:
                    return Mock(returncode=0, stdout="sigstore 4.4.0\n", stderr="")
                bundle_path = Path(command[command.index("--bundle") + 1])
                bundle_path.write_text(json.dumps(real_bundle), encoding="utf-8")
                return Mock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value="/usr/bin/sigstore"), patch(
                "subprocess.run", side_effect=fake_run
            ) as run:
                result = attest_keyless(
                    evidence,
                    identity_token="test-token",
                    oauth_force_oob=True,
                    staging=True,
                )

        self.assertEqual(result, real_bundle)
        attest_command = run.call_args_list[1].args[0]
        self.assertEqual(
            attest_command[:3], ["/usr/bin/sigstore", "--staging", "attest"]
        )
        self.assertIn("--oidc-disable-ambient-providers", attest_command)
        self.assertIn("--identity-token", attest_command)
        self.assertIn("--oauth-force-oob", attest_command)

    def test_sigstore_flow_error_returns_structured_fail_closed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")

            def fake_run(command, **kwargs):
                if "--version" in command:
                    return Mock(returncode=0, stdout="sigstore 4.4.0\n", stderr="")
                return Mock(returncode=1, stdout="", stderr="login failed")

            with patch("shutil.which", return_value="/usr/bin/sigstore"), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = attest_keyless(evidence, identity_token="expired-token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_KEYLESS_ATTEST_FAILED"
        )
        self.assertFalse(result["keyless_identity"])

    def test_identity_token_environment_passthrough_is_explicit(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")

            def fake_run(command, **kwargs):
                if "--version" in command:
                    return Mock(returncode=0, stdout="sigstore 4.4.0\n", stderr="")
                Path(command[command.index("--bundle") + 1]).write_text(
                    json.dumps(real_bundle), encoding="utf-8"
                )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value="/usr/bin/sigstore"), patch(
                "subprocess.run", side_effect=fake_run
            ) as run:
                result = attest_keyless(
                    evidence,
                    environ={"SIGSTORE_IDENTITY_TOKEN": "environment-token"},
                )

        self.assertEqual(result, real_bundle)
        attest_command = run.call_args_list[1].args[0]
        self.assertEqual(
            attest_command[attest_command.index("--identity-token") + 1],
            "environment-token",
        )

    def test_tool_absent_returns_structured_fail_closed_error(self) -> None:
        with patch("shutil.which", return_value=None):
            result = attest_keyless("evidence.json", identity_token="token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_SIGSTORE_UNAVAILABLE"
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["keyless_identity"])

    def test_noninteractive_without_token_never_invokes_sigstore(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/sigstore"), patch(
            "sys.stdin.isatty", return_value=False
        ), patch.dict(
            os.environ, {"SIGSTORE_IDENTITY_TOKEN": ""}, clear=False
        ), patch("subprocess.run") as run:
            result = attest_keyless("evidence.json")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_KEYLESS_NONINTERACTIVE"
        )
        self.assertFalse(result["keyless_identity"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
