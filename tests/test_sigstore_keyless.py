from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from witnessd.adapters import sigstore_keyless
from witnessd.adapters.sigstore_keyless import attest_keyless

FIXTURES = Path(__file__).parent / "fixtures" / "sigstore-keyless"
HELPER = Path(sigstore_keyless.__file__).with_name("_keyless_sign_helper.py")


def _write_console_script(
    directory: str, interpreter: str = "/usr/bin/python3"
) -> Path:
    console_script = Path(directory) / "sigstore"
    console_script.write_text(f"#!{interpreter}\n", encoding="utf-8")
    console_script.chmod(0o755)
    return console_script


def _write_fake_sigstore(directory: str) -> Path:
    package = Path(directory) / "sigstore"
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "4.4.0"\n')
    (package / "dsse.py").write_text(
        textwrap.dedent("""
            class DigestSet:
                def __init__(self, *, root):
                    self.root = root

            class Subject:
                def __init__(self, *, name, digest):
                    self.name = name
                    self.digest = digest

            class StatementBuilder:
                def __init__(self, subjects):
                    self.value = {"subject": [
                        {"name": item.name, "digest": item.digest.root}
                        for item in subjects
                    ]}

                def predicate_type(self, value):
                    self.value["predicateType"] = value
                    return self

                def predicate(self, value):
                    self.value["predicate"] = value
                    return self

                def build(self):
                    return self.value
            """),
        encoding="utf-8",
    )
    (package / "oidc.py").write_text(
        textwrap.dedent("""
            class IdentityToken:
                def __init__(self, raw):
                    self.raw = raw

            class Issuer:
                def __init__(self, url):
                    self.url = url

                def identity_token(self, *, force_oob):
                    return IdentityToken(f"interactive:{self.url}:{force_oob}")
            """),
        encoding="utf-8",
    )
    (package / "models.py").write_text(
        textwrap.dedent("""
            class _SigningConfig:
                def __init__(self, oidc_url):
                    self._oidc_url = oidc_url

                def get_oidc_url(self):
                    return self._oidc_url

            class ClientTrustConfig:
                def __init__(self, instance):
                    self.instance = instance
                    self.signing_config = _SigningConfig(
                        f"https://oidc.{instance}.example/auth"
                    )

                @classmethod
                def staging(cls):
                    return cls("staging")

                @classmethod
                def production(cls):
                    return cls("production")
            """),
        encoding="utf-8",
    )
    (package / "sign.py").write_text(
        textwrap.dedent("""
            import json
            import os

            class _Bundle:
                def __init__(self, statement, token, instance):
                    self.statement = statement
                    self.token = token
                    self.instance = instance

                def to_json(self):
                    return json.dumps({
                        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                        "verificationMaterial": {},
                        "dsseEnvelope": {},
                        "testStatement": self.statement,
                        "testToken": self.token.raw,
                        "testInstance": self.instance,
                    })

            class _Signer:
                def __init__(self, token, instance):
                    self.token = token
                    self.instance = instance

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def sign_dsse(self, statement):
                    if os.environ.get("FAKE_SIGSTORE_FAIL"):
                        raise RuntimeError("fake signing failure")
                    return _Bundle(statement, self.token, self.instance)

            class SigningContext:
                def __init__(self, trust_config):
                    self.trust_config = trust_config

                @classmethod
                def from_trust_config(cls, trust_config):
                    return cls(trust_config)

                def signer(self, token):
                    return _Signer(token, self.trust_config.instance)
            """),
        encoding="utf-8",
    )
    return package.parent


class SigstoreKeylessAdapterTests(unittest.TestCase):
    def test_attest_returns_parsed_real_bundle_and_invokes_library_helper(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.whl"
            evidence.write_bytes(
                (FIXTURES / "evidence-sigstore-4.4.0.whl").read_bytes()
            )
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                if command[1] in {"-c", "--version"}:
                    return Mock(returncode=0, stdout="4.4.0\n", stderr="")
                bundle_path = Path(command[command.index("--bundle") + 1])
                bundle_path.write_text(json.dumps(real_bundle), encoding="utf-8")
                return Mock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ) as run:
                result = attest_keyless(
                    evidence,
                    identity_token="test-token",
                    oauth_force_oob=True,
                    staging=True,
                )

        self.assertEqual(result, real_bundle)
        version_command = run.call_args_list[0].args[0]
        self.assertEqual(version_command[0], "/usr/bin/python3")
        self.assertEqual(version_command[1], "-c")
        self.assertIn("import sigstore", version_command[2])
        helper_call = run.call_args_list[1]
        helper_command = helper_call.args[0]
        self.assertEqual(helper_command[:2], ["/usr/bin/python3", str(HELPER)])
        self.assertEqual(
            helper_command[helper_command.index("--evidence") + 1], str(evidence)
        )
        self.assertIn("--oidc-disable-ambient-providers", helper_command)
        self.assertIn("--identity-token-stdin", helper_command)
        self.assertIn("--staging", helper_command)
        self.assertIn("--oauth-force-oob", helper_command)
        self.assertNotIn("attest", helper_command)
        self.assertNotIn("--predicate-type", helper_command)
        self.assertEqual(
            json.loads(helper_call.kwargs["input"]), {"identity_token": "test-token"}
        )

    def test_sigstore_flow_error_returns_structured_fail_closed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                if command[1] in {"-c", "--version"}:
                    return Mock(returncode=0, stdout="4.4.0\n", stderr="")
                return Mock(returncode=1, stdout="", stderr="login failed")

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = attest_keyless(evidence, identity_token="expired-token")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_KEYLESS_ATTEST_FAILED")
        self.assertFalse(result["keyless_identity"])

    def test_unsupported_sigstore_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)
            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run",
                return_value=Mock(returncode=0, stdout="3.9.0\n", stderr=""),
            ) as run:
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_SIGSTORE_VERSION_UNSUPPORTED"
        )
        self.assertEqual(run.call_count, 1)

    def test_missing_evidence_fails_closed_before_version_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            console_script = _write_console_script(tmp)
            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run"
            ) as run:
                result = attest_keyless("missing.json", identity_token="token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_KEYLESS_EVIDENCE_MISSING"
        )
        run.assert_not_called()

    def test_helper_timeout_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                if command[1] == "-c":
                    return Mock(returncode=0, stdout="4.4.0\n", stderr="")
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_KEYLESS_ATTEST_TIMEOUT")

    def test_missing_helper_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                return Mock(returncode=0, stdout="4.4.0\n", stderr="")

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_KEYLESS_BUNDLE_INVALID")

    def test_helper_bundle_missing_v03_fields_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                if command[1] == "-c":
                    return Mock(returncode=0, stdout="4.4.0\n", stderr="")
                Path(command[command.index("--bundle") + 1]).write_text(
                    json.dumps({"mediaType": "v0.3"}), encoding="utf-8"
                )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_KEYLESS_BUNDLE_INVALID")

    def test_identity_token_environment_passthrough_is_explicit(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp)

            def fake_run(command, **kwargs):
                if command[1] in {"-c", "--version"}:
                    return Mock(returncode=0, stdout="4.4.0\n", stderr="")
                Path(command[command.index("--bundle") + 1]).write_text(
                    json.dumps(real_bundle), encoding="utf-8"
                )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=fake_run
            ) as run:
                result = attest_keyless(
                    evidence,
                    environ={"SIGSTORE_IDENTITY_TOKEN": "environment-token"},
                )

        self.assertEqual(result, real_bundle)
        helper_call = run.call_args_list[1]
        self.assertEqual(
            json.loads(helper_call.kwargs["input"]),
            {"identity_token": "environment-token"},
        )
        self.assertNotIn("environment-token", helper_call.args[0])

    def test_tool_absent_returns_structured_fail_closed_error(self) -> None:
        with patch("shutil.which", return_value=None):
            result = attest_keyless("evidence.json", identity_token="token")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_SIGSTORE_UNAVAILABLE")
        self.assertFalse(result["ok"])
        self.assertFalse(result["keyless_identity"])

    def test_unreadable_console_script_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            with patch("shutil.which", return_value="/missing/sigstore"), patch(
                "subprocess.run"
            ) as run:
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_SIGSTORE_VERSION_CHECK_FAILED"
        )
        run.assert_not_called()

    def test_interpreter_import_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            console_script = _write_console_script(tmp, "/missing/python")
            with patch("shutil.which", return_value=str(console_script)), patch(
                "subprocess.run", side_effect=OSError
            ):
                result = attest_keyless(evidence, identity_token="token")

        self.assertEqual(
            result["error"]["code"], "ERR_WITNESSD_SIGSTORE_VERSION_CHECK_FAILED"
        )

    def test_noninteractive_without_token_never_invokes_sigstore(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/sigstore"), patch(
            "sys.stdin.isatty", return_value=False
        ), patch.dict(os.environ, {"SIGSTORE_IDENTITY_TOKEN": ""}, clear=False), patch(
            "subprocess.run"
        ) as run:
            result = attest_keyless("evidence.json")

        self.assertEqual(result["error"]["code"], "ERR_WITNESSD_KEYLESS_NONINTERACTIVE")
        self.assertFalse(result["keyless_identity"])
        run.assert_not_called()


class SigstoreKeylessHelperTests(unittest.TestCase):
    def _run_helper(
        self,
        root: str,
        evidence: Path,
        bundle: Path,
        *extra: str,
        input_text: str | None = None,
        fail: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        fake_root = _write_fake_sigstore(root)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(fake_root)
        environment["PYTHONNOUSERSITE"] = "1"
        if fail:
            environment["FAKE_SIGSTORE_FAIL"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--evidence",
                str(evidence),
                "--bundle",
                str(bundle),
                "--oidc-disable-ambient-providers",
                *extra,
            ],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def test_helper_builds_honest_custom_statement_with_explicit_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.bin"
            evidence.write_bytes(b"witnessd evidence\n")
            bundle = Path(tmp) / "bundle.json"
            completed = self._run_helper(
                tmp,
                evidence,
                bundle,
                "--identity-token-stdin",
                input_text=json.dumps({"identity_token": "explicit-token"}),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            emitted = json.loads(bundle.read_text(encoding="utf-8"))

        statement = emitted["testStatement"]
        self.assertEqual(
            statement["predicateType"],
            "https://moonweave.dev/witnessd/keyless-evidence-anchor/v1",
        )
        self.assertEqual(
            statement["predicate"],
            {
                "kind": "witnessd-keyless-emission",
                "schema_version": "1.0",
                "raises_assurance": False,
            },
        )
        self.assertEqual(statement["subject"][0]["name"], "evidence.bin")
        self.assertEqual(
            statement["subject"][0]["digest"],
            {"sha256": hashlib.sha256(b"witnessd evidence\n").hexdigest()},
        )
        self.assertEqual(emitted["testToken"], "explicit-token")
        self.assertEqual(emitted["testInstance"], "production")

    def test_helper_mirrors_staging_interactive_issuer_and_force_oob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.bin"
            evidence.write_bytes(b"evidence")
            bundle = Path(tmp) / "bundle.json"
            completed = self._run_helper(
                tmp,
                evidence,
                bundle,
                "--staging",
                "--oauth-force-oob",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            emitted = json.loads(bundle.read_text(encoding="utf-8"))

        self.assertEqual(emitted["testInstance"], "staging")
        self.assertEqual(
            emitted["testToken"],
            "interactive:https://oidc.staging.example/auth:True",
        )

    def test_helper_rejects_missing_no_ambient_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = _write_fake_sigstore(tmp)
            evidence = Path(tmp) / "evidence.bin"
            evidence.write_bytes(b"evidence")
            bundle = Path(tmp) / "bundle.json"
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(fake_root)
            environment["PYTHONNOUSERSITE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--evidence",
                    str(evidence),
                    "--bundle",
                    str(bundle),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(bundle.exists())
            error = json.loads(completed.stderr)
            self.assertEqual(error["error"]["code"], "ERR_KEYLESS_HELPER_FAILED")

    def test_helper_failure_is_structured_and_never_overwrites_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.bin"
            evidence.write_bytes(b"evidence")
            bundle = Path(tmp) / "bundle.json"
            bundle.write_text("existing-valid-bundle", encoding="utf-8")
            completed = self._run_helper(
                tmp,
                evidence,
                bundle,
                "--identity-token-stdin",
                input_text=json.dumps({"identity_token": "explicit-token"}),
                fail=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            error = json.loads(completed.stderr)
            self.assertEqual(error["error"]["code"], "ERR_KEYLESS_HELPER_FAILED")
            self.assertEqual(
                bundle.read_text(encoding="utf-8"), "existing-valid-bundle"
            )


if __name__ == "__main__":
    unittest.main()
