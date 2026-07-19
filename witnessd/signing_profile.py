"""Signing profile selection for witnessd evidence bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATOR_KEY_PROFILE = "operator-key"
KEYLESS_FULCIO_REKOR_PROFILE = "keyless-fulcio-rekor"


class SigningProfileError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SigningProfile:
    name: str
    signing_status: str
    signature_boundary: dict[str, Any]


def operator_key_signature_boundary() -> dict[str, Any]:
    return {
        "scheme": "DSSE-Ed25519-openssl-cli",
        "operator_key": True,
        "public_verifiable": True,
        "keyless_identity": False,
        "transparency_logged": False,
        "note": (
            "Trust is rooted in the operator-held key and distributed public "
            "key; this is not Fulcio keyless identity or Rekor logging."
        ),
    }


def keyless_signature_boundary() -> dict[str, Any]:
    return {
        "scheme": "DSSE-Sigstore-Fulcio-Rekor",
        "operator_key": False,
        "public_verifiable": True,
        "keyless_identity": True,
        "transparency_logged": True,
        "raises_assurance": False,
        "note": (
            "This orthogonal public signing anchor does not raise the "
            "A0/A1/A2 observation-assurance level."
        ),
    }


def select_signing_profile(requested: str | None) -> SigningProfile:
    profile = requested or OPERATOR_KEY_PROFILE
    if profile == OPERATOR_KEY_PROFILE:
        return SigningProfile(
            name=OPERATOR_KEY_PROFILE,
            signing_status="signed-ed25519-operator-key",
            signature_boundary=operator_key_signature_boundary(),
        )
    if profile == KEYLESS_FULCIO_REKOR_PROFILE:
        return SigningProfile(
            name=KEYLESS_FULCIO_REKOR_PROFILE,
            signing_status="signed-keyless-fulcio-rekor",
            signature_boundary=keyless_signature_boundary(),
        )
    raise SigningProfileError("ERR_WITNESSD_SIGNING_PROFILE_UNSUPPORTED")
