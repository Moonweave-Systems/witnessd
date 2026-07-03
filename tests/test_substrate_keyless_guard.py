import tempfile
import unittest
from pathlib import Path

from witnessd.signing_profile import (
    KEYLESS_FULCIO_REKOR_PROFILE,
    SigningProfileError,
)
from witnessd.substrate import build_bundle


class TestSubstrateKeylessGuard(unittest.TestCase):
    def test_build_bundle_rejects_keyless_profile_until_live_verifier_exists(self):
        manifest = {
            "kind": "capture-manifest",
            "assurance": "A2-isolated-observed",
            "decision": "accepted",
            "prev_capture_hash": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            with self.assertRaises(SigningProfileError) as cm:
                build_bundle(
                    manifest,
                    {"artifact": str(artifact)},
                    signing_profile=KEYLESS_FULCIO_REKOR_PROFILE,
                )
            self.assertEqual(cm.exception.code, "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED")

    def test_unsigned_default_still_works(self):
        manifest = {
            "kind": "capture-manifest",
            "assurance": "A2-isolated-observed",
            "decision": "accepted",
            "prev_capture_hash": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            bundle = build_bundle(manifest, {"artifact": str(artifact)})
            self.assertEqual(bundle["signing_status"], "unsigned-content-addressed")
            self.assertNotIn("signature_boundary", bundle)


if __name__ == "__main__":
    unittest.main()
