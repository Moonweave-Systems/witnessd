from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from depone.agent_fabric.keyless_verify import verify_keyless_bundle

try:
    import cryptography  # noqa: F401

    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

from witnessd.signing import gen_operator_keypair
from witnessd.signing_profile import KEYLESS_FULCIO_REKOR_PROFILE
from witnessd.substrate import build_bundle


FIXTURES = Path(__file__).parent / "fixtures" / "sigstore-keyless"


def _manifest() -> dict:
    return {
        "kind": "capture-manifest",
        "assurance": "A2-isolated-observed",
        "decision": "accepted",
        "prev_capture_hash": None,
    }


class TestSubstrateKeylessGuard(unittest.TestCase):
    def test_keyless_requires_existing_operator_signature(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        evidence_path = FIXTURES / "evidence-sigstore-4.4.0.whl"
        stderr = io.StringIO()
        with (
            patch(
                "witnessd.substrate.sigstore_keyless.attest_keyless",
                return_value=real_bundle,
            ) as attest,
            redirect_stderr(stderr),
        ):
            substrate = build_bundle(
                _manifest(),
                {"evidence": str(evidence_path)},
                signing_profile=KEYLESS_FULCIO_REKOR_PROFILE,
                keyless_evidence_path=str(evidence_path),
            )

        self.assertEqual(substrate["signing_status"], "unsigned-content-addressed")
        self.assertNotIn("keyless_attestation", substrate)
        self.assertNotIn("signature_boundary", substrate)
        self.assertIn(
            "ERR_WITNESSD_KEYLESS_OPERATOR_SIGNATURE_REQUIRED", stderr.getvalue()
        )
        attest.assert_not_called()

    @unittest.skipUnless(
        HAS_CRYPTOGRAPHY, "requires cryptography (Depone[keyless] verify path)"
    )
    def test_real_bundle_sidecar_passes_depone_offline_verifier(self) -> None:
        real_bundle = json.loads((FIXTURES / "real-bundle.json").read_text())
        evidence_path = FIXTURES / "evidence-sigstore-4.4.0.whl"
        policy = json.loads((FIXTURES / "identity-policy.json").read_text())
        trusted_root = json.loads((FIXTURES / "prod-trusted-root.json").read_text())

        with tempfile.TemporaryDirectory() as tmp:
            private_key, public_key = gen_operator_keypair(tmp)
            with patch(
                "witnessd.substrate.sigstore_keyless.attest_keyless",
                return_value=real_bundle,
            ):
                substrate = build_bundle(
                    _manifest(),
                    {"evidence": str(evidence_path)},
                    private_key,
                    public_key,
                    signing_profile=KEYLESS_FULCIO_REKOR_PROFILE,
                    keyless_evidence_path=str(evidence_path),
                    keyless_options={"identity_token": "fixture-token"},
                )
            operator = build_bundle(
                _manifest(),
                {"evidence": str(evidence_path)},
                private_key,
                public_key,
            )

        verdict = verify_keyless_bundle(
            substrate["keyless_attestation"],
            evidence_path.read_bytes(),
            policy,
            trusted_root,
        )
        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(verdict["anchor_class"], "keyless-transparency-logged")
        self.assertEqual(substrate["signing_status"], "signed-keyless-fulcio-rekor")
        self.assertTrue(substrate["dsse_envelope"]["signatures"])
        self.assertTrue(substrate["signature_boundary"]["keyless_identity"])
        self.assertFalse(substrate["signature_boundary"].get("raises_assurance", False))
        self.assertEqual(substrate["assurance"], operator["assurance"])

    def test_failed_keyless_emission_falls_back_without_fake_boundary(self) -> None:
        error = {
            "ok": False,
            "keyless_identity": False,
            "error": {
                "code": "ERR_WITNESSD_SIGSTORE_UNAVAILABLE",
                "message": "missing",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            private_key, public_key = gen_operator_keypair(tmp)
            stderr = io.StringIO()
            with (
                patch(
                    "witnessd.substrate.sigstore_keyless.attest_keyless",
                    return_value=error,
                ),
                redirect_stderr(stderr),
            ):
                bundle = build_bundle(
                    _manifest(),
                    {"artifact": str(artifact)},
                    private_key,
                    public_key,
                    signing_profile=KEYLESS_FULCIO_REKOR_PROFILE,
                    keyless_evidence_path=str(artifact),
                )

        self.assertEqual(bundle["signing_status"], "signed-ed25519-operator-key")
        self.assertNotIn("keyless_attestation", bundle)
        self.assertFalse(bundle["signature_boundary"]["keyless_identity"])
        self.assertIn("ERR_WITNESSD_SIGSTORE_UNAVAILABLE", stderr.getvalue())

    def test_default_operator_bundle_is_byte_identical_to_explicit_profile(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            private_key, public_key = gen_operator_keypair(tmp)
            default = build_bundle(
                _manifest(), {"artifact": str(artifact)}, private_key, public_key
            )
            explicit = build_bundle(
                _manifest(),
                {"artifact": str(artifact)},
                private_key,
                public_key,
                signing_profile="operator-key",
            )

        self.assertEqual(
            json.dumps(default, sort_keys=True, separators=(",", ":")).encode(),
            json.dumps(explicit, sort_keys=True, separators=(",", ":")).encode(),
        )
        self.assertNotIn("keyless_attestation", default)


if __name__ == "__main__":
    unittest.main()
