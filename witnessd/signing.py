"""Ed25519 DSSE signing via the openssl CLI (E6/E7).

witnessd signs DSSE envelopes with an operator-held Ed25519 key so Depone's
`sign.verify_dsse_envelope` re-derives the signature from the emitted bytes.
The PAE encoding, openssl invocation, and signature-record shape mirror Depone's
`sign.sign_dsse_envelope` exactly — any divergence would make Depone reject the
envelope. Trust is rooted in the distributed public key (operator key, not
keyless Fulcio identity, not Rekor-logged).

Runtime is stdlib-only; the crypto is shelled out to `openssl`.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from typing import Any

ERR_OPENSSL_UNAVAILABLE = "ERR_OPENSSL_UNAVAILABLE"
ERR_DSSE_SIGN_FAILED = "ERR_DSSE_SIGN_FAILED"


class DsseSigningError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _openssl_path() -> str | None:
    return shutil.which("openssl")


def _require_openssl() -> str:
    openssl = _openssl_path()
    if openssl is None:
        raise DsseSigningError(
            ERR_OPENSSL_UNAVAILABLE, "openssl executable not found on PATH"
        )
    return openssl


def dsse_pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE v1 Pre-Authentication Encoding — byte-identical to Depone's dsse_pae."""
    return (
        b"DSSEv1 "
        + str(len(payload_type)).encode("ascii")
        + b" "
        + payload_type.encode("utf-8")
        + b" "
        + str(len(payload)).encode("ascii")
        + b" "
        + payload
    )


def gen_operator_keypair(out_dir: str) -> tuple[str, str]:
    """Generate an ephemeral Ed25519 keypair, returning (private_path, public_path)."""
    openssl = _require_openssl()
    private_key = os.path.join(out_dir, "operator-ed25519.pem")
    public_key = os.path.join(out_dir, "operator-ed25519.pub.pem")
    for command in (
        [openssl, "genpkey", "-algorithm", "Ed25519", "-out", private_key],
        [openssl, "pkey", "-in", private_key, "-pubout", "-out", public_key],
    ):
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            message = (
                result.stderr or result.stdout or "openssl keygen failed"
            ).strip()
            raise DsseSigningError(ERR_DSSE_SIGN_FAILED, message)
    return private_key, public_key


def _decode_payload(envelope: dict[str, Any]) -> tuple[str, bytes]:
    payload_type = envelope.get("payloadType")
    payload = envelope.get("payload")
    if not isinstance(payload_type, str) or not payload_type:
        raise DsseSigningError(ERR_DSSE_SIGN_FAILED, "payloadType must be non-empty")
    if not isinstance(payload, str):
        raise DsseSigningError(ERR_DSSE_SIGN_FAILED, "payload must be base64 text")
    try:
        return payload_type, base64.b64decode(payload.encode("ascii"), validate=True)
    except Exception as exc:
        raise DsseSigningError(
            ERR_DSSE_SIGN_FAILED, "payload is not valid base64"
        ) from exc


def sign_dsse(
    envelope: dict[str, Any], private_key_path: str, *, key_id: str
) -> dict[str, Any]:
    """Return a copy of the DSSE envelope with an operator Ed25519 signature.

    The PAE is signed with `openssl pkeyutl -sign -rawin` so Depone's
    `verify_dsse_envelope` (same PAE, same raw Ed25519 verify) accepts it.
    """
    openssl = _require_openssl()
    if not isinstance(key_id, str) or not key_id:
        raise DsseSigningError(ERR_DSSE_SIGN_FAILED, "key_id must be non-empty")
    payload_type, payload = _decode_payload(envelope)
    pae = dsse_pae(payload_type, payload)

    with tempfile.TemporaryDirectory() as temp_dir:
        pae_path = os.path.join(temp_dir, "payload.pae")
        sig_path = os.path.join(temp_dir, "payload.sig")
        with open(pae_path, "wb") as handle:
            handle.write(pae)
        result = subprocess.run(
            [
                openssl,
                "pkeyutl",
                "-sign",
                "-inkey",
                private_key_path,
                "-rawin",
                "-in",
                pae_path,
                "-out",
                sig_path,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not os.path.exists(sig_path):
            message = (
                result.stderr or result.stdout or "openssl signing failed"
            ).strip()
            raise DsseSigningError(ERR_DSSE_SIGN_FAILED, message)
        with open(sig_path, "rb") as handle:
            signature = handle.read()

    signed = dict(envelope)
    signed["signatures"] = [
        {
            "keyid": key_id,
            "sig": base64.b64encode(signature).decode("ascii"),
        }
    ]
    return signed


def _self_test() -> None:
    if _openssl_path() is None:
        print("witnessd signing --self-test: pass (openssl unavailable)")
        return
    if dsse_pae("x", b"abc") != b"DSSEv1 1 x 3 abc":
        raise AssertionError("DSSE PAE vector mismatch")
    from depone.agent_fabric.sign import verify_dsse_envelope

    with tempfile.TemporaryDirectory() as temp_dir:
        priv, pub = gen_operator_keypair(temp_dir)
        env = sign_dsse(
            {"payloadType": "application/vnd.in-toto+json", "payload": "e30="},
            priv,
            key_id="operator-self-test",
        )
        if not verify_dsse_envelope(env, pub):
            raise AssertionError("signed envelope should verify")
        env["payload"] = "eyJ4IjoxfQ=="
        if verify_dsse_envelope(env, pub):
            raise AssertionError("tampered payload must not verify")
    print("witnessd signing --self-test: pass")
