"""W1 negative/tamper fixtures must be *detected* by Depone.

Each committed fixture under ``fixtures/w1/negative/`` is a targeted forgery of a
single byte-group in an otherwise-valid W1 artifact. Depone (the separate,
non-executing validator) must reject every one: capture-manifest tampers surface
as validation errors, and a forged ``A3-*`` assurance fails signature
verification. This is the regression floor for the "완료=관측자-서명 바이트"
thesis — if any tamper ever slips past Depone, these fail.
"""

import json
import shutil
import unittest
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.sign import verify_signed_bundle

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "w1"
NEG = FIX / "negative"


def _load(name: str) -> dict:
    with open(NEG / name, encoding="utf-8") as handle:
        return json.load(handle)


class TestW1NegativeManifests(unittest.TestCase):
    def test_observer_capture_hash_mismatch_detected(self):
        errors = validate_capture_manifest(_load("observer_capture_hash_mismatch.json"))
        self.assertIn("observer_capture_hash mismatch", errors)

    def test_stale_source_fixture_hash_detected(self):
        errors = validate_capture_manifest(_load("stale_source_fixture_hash.json"))
        self.assertIn("observer_capture.source_fixture_hash is stale", errors)

    def test_unexpected_touched_files_detected(self):
        errors = validate_capture_manifest(_load("unexpected_touched_files.json"))
        self.assertTrue(
            any("unexpected touched files" in error for error in errors),
            f"expected an unexpected-touched-files error, got {errors!r}",
        )


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestW1ForgedBundle(unittest.TestCase):
    def test_forged_a3_fails_signature_verification(self):
        bundle = _load("forged_a3.json")
        self.assertEqual(bundle["assurance"], "A3-fabricated-observed")
        public_key_path = str(FIX / "keys" / "operator.pub")
        self.assertFalse(verify_signed_bundle(bundle, public_key_path))


if __name__ == "__main__":
    unittest.main()
