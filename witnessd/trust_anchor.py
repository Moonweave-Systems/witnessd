"""Classify the provenance of the public key used by verifier subprocesses."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


TRUSTED_OBSERVER_PUBLIC_KEY_ENV = "DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"
TRUST_ANCHOR_SELF_SIGNED = "self-signed"
TRUST_ANCHOR_OPERATOR_PROVIDED = "operator-provided"
TRUST_ANCHOR_KEYLESS_TRANSPARENCY_LOGGED = "keyless-transparency-logged"
_RUNTIME_DEFAULT_PUBLIC_KEYS: dict[Path, str] = {}


@dataclass(frozen=True)
class TrustAnchor:
    public_key_path: Path
    trust_anchor: str

    @property
    def independent(self) -> bool:
        return self.trust_anchor in {
            TRUST_ANCHOR_OPERATOR_PROVIDED,
            TRUST_ANCHOR_KEYLESS_TRANSPARENCY_LOGGED,
        }


def resolve_bundle_trust_anchor(
    bundle: Mapping[str, object], *, fallback: TrustAnchor
) -> TrustAnchor:
    """Classify an emitted real-bundle sidecar without changing verifier truth."""

    boundary = bundle.get("signature_boundary")
    attestation = bundle.get("keyless_attestation")
    if (
        bundle.get("signing_status") == "signed-keyless-fulcio-rekor"
        and isinstance(boundary, dict)
        and boundary.get("keyless_identity") is True
        and boundary.get("transparency_logged") is True
        and isinstance(attestation, dict)
        and attestation.get("mediaType")
        == "application/vnd.dev.sigstore.bundle.v0.3+json"
    ):
        return TrustAnchor(
            fallback.public_key_path,
            TRUST_ANCHOR_KEYLESS_TRANSPARENCY_LOGGED,
        )
    return fallback


def resolve_trust_anchor(
    *,
    home: Path | None = None,
    runtime_public_key: Path | None = None,
    runtime_generated: bool = False,
    environ: Mapping[str, str] | None = None,
) -> TrustAnchor:
    """Resolve the verifier key and label whether the operator supplied it.

    An environment-selected key is operator-provided unless it resolves to the
    runtime's own key, or this invocation just generated an identical key.
    Falling back to the runtime/home key is always self-signed.
    """
    environment = os.environ if environ is None else environ
    configured = environment.get(TRUSTED_OBSERVER_PUBLIC_KEY_ENV)
    runtime_key = (
        runtime_public_key.resolve(strict=False)
        if runtime_public_key is not None
        else None
    )
    if configured and not is_runtime_default_public_key(Path(configured)):
        configured_key = Path(configured).expanduser().resolve(strict=False)
        runtime_home_key = (
            (home / "keys" / "operator-ed25519.pub.pem").resolve(strict=False)
            if home is not None
            else None
        )
        same_runtime_path = configured_key == runtime_home_key or (
            runtime_generated and configured_key == runtime_key
        )
        same_just_generated_key = (
            runtime_generated
            and runtime_key is not None
            and _same_file_bytes(configured_key, runtime_key)
        )
        trust_anchor = (
            TRUST_ANCHOR_SELF_SIGNED
            if same_runtime_path or same_just_generated_key
            else TRUST_ANCHOR_OPERATOR_PROVIDED
        )
        return TrustAnchor(configured_key, trust_anchor)

    if runtime_key is not None:
        return TrustAnchor(runtime_key, TRUST_ANCHOR_SELF_SIGNED)
    if home is None:
        raise ValueError("home or runtime_public_key is required")
    public_key = (home / "keys" / "operator-ed25519.pub.pem").resolve(strict=False)
    return TrustAnchor(public_key, TRUST_ANCHOR_SELF_SIGNED)
def _same_file_bytes(left: Path, right: Path) -> bool:
    try:
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def record_runtime_default_public_key(path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    fingerprint = _file_sha256(resolved)
    if fingerprint is not None:
        _RUNTIME_DEFAULT_PUBLIC_KEYS[resolved] = fingerprint


def is_runtime_default_public_key(path: Path) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    recorded = _RUNTIME_DEFAULT_PUBLIC_KEYS.get(resolved)
    current = _file_sha256(resolved)
    return recorded is not None and (current is None or current == recorded)


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
