import hashlib
import inspect
import unittest

from witnessd.adapter_run import run_adapter_lane
from witnessd.emitter import emit_lane_evidence, emit_supervised_lane
from witnessd.privacy import (
    CAPTURE_PROFILE_REDACTED,
    redact_secrets,
    redact_secrets_in,
)
from witnessd.runintent import build_run_intent


class TestSecretScrub(unittest.TestCase):
    def test_redact_secrets_replaces_only_high_confidence_patterns(self):
        secrets = {
            "pem_private_key": (
                "-----BEGIN PRIVATE KEY-----\n"
                "planted-private-key-material\n"
                "-----END PRIVATE KEY-----"
            ),
            "openai_key": "sk-" + "a" * 32,
            "github_pat_classic": "ghp_" + "b" * 36,
            "github_pat_fine": "github_pat_" + "c" * 30,
            "aws_access_key": "AKIA" + "D" * 16,
            "slack_token": "xoxb-" + "e" * 24,
            "bearer_token": "f" * 32,
        }
        text = "\n".join(
            [
                secrets["pem_private_key"],
                secrets["openai_key"],
                secrets["github_pat_classic"],
                secrets["github_pat_fine"],
                secrets["aws_access_key"],
                secrets["slack_token"],
                f"Bearer {secrets['bearer_token']}",
            ]
        )

        scrubbed, findings = redact_secrets(text)

        for secret in secrets.values():
            self.assertNotIn(secret, scrubbed)
        for rule, secret in secrets.items():
            token = f"[REDACTED:{rule}:{hashlib.sha256(secret.encode('utf-8')).hexdigest()[:12]}]"
            self.assertIn(token, scrubbed)
        self.assertIn("Bearer [REDACTED:bearer_token:", scrubbed)
        self.assertEqual(
            {finding["rule"] for finding in findings}, set(secrets)
        )
        self.assertTrue(
            all(len(finding["match_sha256"]) == 64 for finding in findings)
        )
        self.assertTrue(all(finding["count"] == 1 for finding in findings))

    def test_redact_secrets_in_recurses_and_aggregates_repeated_matches(self):
        secret = "sk-" + "z" * 32
        value = {
            "stdout": secret,
            "receipts": [{"stderr": f"failed with {secret}"}],
            "unchanged": 7,
        }

        scrubbed, findings = redact_secrets_in(value)

        self.assertNotIn(secret, str(scrubbed))
        self.assertEqual(scrubbed["unchanged"], 7)
        self.assertEqual(
            findings,
            [
                {
                    "rule": "openai_key",
                    "match_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                    "count": 2,
                }
            ],
        )

    def test_ordinary_output_is_byte_for_byte_unchanged(self):
        ordinary = (
            "build complete\n"
            "password policy loaded; token_count=42\n"
            "request id abcdefghijklmnopqrstuvwxyz\n"
        )

        scrubbed, findings = redact_secrets(ordinary)

        self.assertEqual(scrubbed.encode("utf-8"), ordinary.encode("utf-8"))
        self.assertEqual(findings, [])

    def test_capture_function_defaults_are_redacted(self):
        for function in (
            run_adapter_lane,
            emit_lane_evidence,
            emit_supervised_lane,
            build_run_intent,
        ):
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    inspect.signature(function)
                    .parameters["capture_profile"]
                    .default,
                    CAPTURE_PROFILE_REDACTED,
                )


if __name__ == "__main__":
    unittest.main()
