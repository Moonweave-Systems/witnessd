import unittest

from witnessd.signing_profile import (
    KEYLESS_FULCIO_REKOR_PROFILE,
    OPERATOR_KEY_PROFILE,
    SigningProfileError,
    select_signing_profile,
)


class TestSigningProfile(unittest.TestCase):
    def test_default_profile_is_operator_key(self):
        profile = select_signing_profile(None)
        self.assertEqual(profile.name, OPERATOR_KEY_PROFILE)
        self.assertEqual(profile.signing_status, "signed-ed25519-operator-key")
        self.assertFalse(profile.signature_boundary["keyless_identity"])
        self.assertFalse(profile.signature_boundary["transparency_logged"])

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(SigningProfileError) as cm:
            select_signing_profile("unknown")
        self.assertEqual(cm.exception.code, "ERR_WITNESSD_SIGNING_PROFILE_UNSUPPORTED")

    def test_keyless_profile_is_blocked_until_live_verifier_exists(self):
        with self.assertRaises(SigningProfileError) as cm:
            select_signing_profile(KEYLESS_FULCIO_REKOR_PROFILE)
        self.assertEqual(cm.exception.code, "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED")


if __name__ == "__main__":
    unittest.main()
