import base64
import tempfile
import unittest

from witnessd.signing import (
    ERR_OPENSSL_UNAVAILABLE,
    ERR_OPERATOR_KEY_CONFLICT,
    DsseSigningError,
    derive_public_key_id,
    gen_operator_keypair,
    sign_dsse,
)
from depone.agent_fabric.sign import verify_dsse_envelope


class TestSign(unittest.TestCase):
    def test_roundtrip_and_forgery(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            env = sign_dsse(
                {"payloadType": "application/vnd.in-toto+json", "payload": "e30="},
                priv,
                key_id="op1",
            )
            self.assertTrue(verify_dsse_envelope(env, pub))
            env["payload"] = "eyJ4IjoxfQ=="  # tamper
            self.assertFalse(verify_dsse_envelope(env, pub))

    def test_wrong_key_rejected(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            priv, _pub = gen_operator_keypair(d)
            _priv2, pub2 = gen_operator_keypair(e)
            env = sign_dsse(
                {"payloadType": "application/vnd.in-toto+json", "payload": "e30="},
                priv,
                key_id="op1",
            )
            self.assertFalse(verify_dsse_envelope(env, pub2))

    def test_signature_record_shape(self):
        with tempfile.TemporaryDirectory() as d:
            priv, _pub = gen_operator_keypair(d)
            env = sign_dsse(
                {"payloadType": "application/vnd.in-toto+json", "payload": "e30="},
                priv,
                key_id="op1",
            )
            self.assertEqual(len(env["signatures"]), 1)
            record = env["signatures"][0]
            self.assertEqual(record["keyid"], "op1")
            base64.b64decode(record["sig"].encode("ascii"), validate=True)

    def test_public_key_id_is_sha256_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            _priv, pub = gen_operator_keypair(d)

            key_id = derive_public_key_id(pub)

            self.assertRegex(key_id, r"^sha256:[0-9a-f]{64}$")

    def test_empty_key_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            priv, _pub = gen_operator_keypair(d)
            with self.assertRaises(DsseSigningError):
                sign_dsse(
                    {"payloadType": "application/vnd.in-toto+json", "payload": "e30="},
                    priv,
                    key_id="",
                )

    def test_error_codes_exposed(self):
        self.assertEqual(ERR_OPENSSL_UNAVAILABLE, "ERR_OPENSSL_UNAVAILABLE")
        self.assertEqual(ERR_OPERATOR_KEY_CONFLICT, "ERR_OPERATOR_KEY_CONFLICT")


if __name__ == "__main__":
    unittest.main()
