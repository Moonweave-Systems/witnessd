"""Trusted observer provenance emission for witnessd runtime.

This is the emit-side copy of Depone's signed trusted-observer provenance
contract. Verification remains outside witnessd runtime.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from witnessd.canonical import canonical_hash
from witnessd.signing import sign_dsse

PROVENANCE_KIND = "trusted-observer-provenance"
PROVENANCE_SCHEMA_VERSION = "1.0"
PROVENANCE_BINDING_KIND = "trusted-observer-provenance-binding"
DSSE_PROVENANCE_PAYLOAD_TYPE = (
    "application/vnd.depone.trusted-observer-provenance.v1+json"
)
DSSE_PROVENANCE_SCHEME = "DSSE-Ed25519-openssl-cli"


def build_signed_trusted_observer_provenance(
    manifest: dict[str, Any],
    *,
    evidence_path: str,
    private_key_path: str,
    key_id: str,
) -> dict[str, Any]:
    """Build an Ed25519 DSSE provenance record for an exact capture manifest."""

    binding = _binding(manifest, evidence_path=evidence_path)
    signed_envelope = sign_dsse(
        _unsigned_dsse_envelope(binding),
        private_key_path,
        key_id=key_id,
    )
    return {
        "kind": PROVENANCE_KIND,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "evidence_path": binding["evidence_path"],
        "manifest_hash": binding["manifest_hash"],
        "observer_capture_hash": binding["observer_capture_hash"],
        "scheme": DSSE_PROVENANCE_SCHEME,
        "key_id": key_id,
        "dsse_envelope": signed_envelope,
    }


def _binding(manifest: dict[str, Any], *, evidence_path: str) -> dict[str, Any]:
    return {
        "kind": PROVENANCE_BINDING_KIND,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "evidence_path": evidence_path,
        "manifest_hash": canonical_hash(manifest),
        "observer_capture_hash": manifest.get("observer_capture_hash"),
    }


def _unsigned_dsse_envelope(binding: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "payloadType": DSSE_PROVENANCE_PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [],
    }
