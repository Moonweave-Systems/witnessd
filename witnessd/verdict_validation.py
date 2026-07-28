"""Authenticate stored proofcheck results without invoking the verifier again."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from witnessd.distribution import PROVISION_KIND
from witnessd.signing import (
    derive_public_key_id,
    sign_dsse,
    verify_dsse,
)


VALIDATION_ARTIFACT = "proofcheck-validation.json"
VALIDATION_KIND = "orro-proofcheck-validation"
VALIDATION_SCHEMA_VERSION = "1.0"
VALIDATION_PAYLOAD_TYPE = "application/vnd.orro.proofcheck-validation.v1+json"

_DERIVED_ARTIFACTS = {
    "companion-manifest.json",
    "orro-auto-plan.json",
    "orro-auto-receipt.json",
    "orro-auto-session.json",
    "orro-continuation-decision.json",
    "orro-handoff.json",
    "orro-report.json",
    "proofcheck-verdict.json",
    VALIDATION_ARTIFACT,
    "ship-receipt.json",
    "team-ledger-verdict.json",
}


def collect_evidence_artifact_hashes(run_dir: Path) -> list[dict[str, str]]:
    """Hash the complete verifier input set, excluding derived result surfaces."""

    hashes: list[dict[str, str]] = []
    for path in sorted(candidate for candidate in run_dir.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(run_dir)
        if relative_path.as_posix() in _DERIVED_ARTIFACTS:
            continue
        hashes.append(
            {
                "path": str(relative_path),
                "sha256": _hash_file(path),
            }
        )
    return hashes


def write_validation_artifact(
    *,
    run_dir: Path,
    home: Path,
    verdict_path: Path,
    verifier_decision: str,
    effective_decision: str,
    composition: dict[str, Any],
) -> Path:
    """Write an operator-signed cache for one actual proofcheck invocation."""

    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False)
    verdict_path = verdict_path.resolve(strict=False)
    expected_verdict = (run_dir / "proofcheck-verdict.json").resolve(strict=False)
    if verdict_path != expected_verdict:
        raise ValueError("validation requires the canonical proofcheck verdict path")

    private_key = home / "keys" / "operator-ed25519.pem"
    public_key = home / "keys" / "operator-ed25519.pub.pem"
    if not private_key.is_file() or not public_key.is_file():
        raise ValueError("operator signing keypair is required for stored verdict validation")

    artifact_hashes = collect_evidence_artifact_hashes(run_dir)
    verifier_commit = _verifier_commit(home)
    signed_payload = {
        "kind": VALIDATION_KIND,
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "evidence_dir": str(run_dir),
        "evidence_digest": _artifact_set_digest(artifact_hashes),
        "artifact_hashes": artifact_hashes,
        "verifier": {
            "component": "depone",
            "commit": verifier_commit,
        },
        "verdict": {
            "path": "proofcheck-verdict.json",
            "sha256": _hash_file(verdict_path),
            "decision": verifier_decision,
            "authorship": "depone",
        },
        "composition": {
            **composition,
            "decision": effective_decision,
            "authorship": "witnessd",
        },
    }
    payload_bytes = _canonical_json(signed_payload)
    envelope = sign_dsse(
        {
            "payloadType": VALIDATION_PAYLOAD_TYPE,
            "payload": base64.b64encode(payload_bytes).decode("ascii"),
            "signatures": [],
        },
        str(private_key),
        key_id=derive_public_key_id(str(public_key)),
    )
    artifact = {
        "kind": VALIDATION_KIND,
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "dsse_envelope": envelope,
    }
    path = run_dir / VALIDATION_ARTIFACT
    path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def validate_stored_verdict(run_dir: Path, *, home: Path | None) -> dict[str, Any]:
    """Validate the signed cache and its bindings, returning a fail-closed state."""

    run_dir = run_dir.resolve(strict=False)
    verdict_path = run_dir / "proofcheck-verdict.json"
    if not verdict_path.is_file():
        return _state("missing", reason="proofcheck verdict is missing")
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _state("unrevalidated", reason=f"proofcheck verdict is unreadable: {exc}")
    if not isinstance(verdict, dict):
        return _state("unrevalidated", reason="proofcheck verdict must be a JSON object")

    cache_path = run_dir / VALIDATION_ARTIFACT
    if not cache_path.is_file():
        return _state("unrevalidated", reason="signed proofcheck validation is missing")
    if home is None:
        return _state("unrevalidated", reason="witnessd home is required to validate the operator signature")

    try:
        artifact = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict) or artifact.get("kind") != VALIDATION_KIND:
            raise ValueError("validation artifact kind is invalid")
        envelope = artifact.get("dsse_envelope")
        if not isinstance(envelope, dict):
            raise ValueError("validation DSSE envelope is missing")
        public_key = home.resolve(strict=False) / "keys" / "operator-ed25519.pub.pem"
        if not public_key.is_file():
            raise ValueError("operator public key is missing")
        signatures = envelope.get("signatures")
        expected_key_id = derive_public_key_id(str(public_key))
        if (
            not isinstance(signatures, list)
            or len(signatures) != 1
            or not isinstance(signatures[0], dict)
            or signatures[0].get("keyid") != expected_key_id
        ):
            raise ValueError("validation signature key does not match the operator key")
        if not verify_dsse(envelope, str(public_key)):
            raise ValueError("validation signature is invalid")
        payload = _decode_payload(envelope)
        if payload.get("kind") != VALIDATION_KIND:
            raise ValueError("signed validation payload kind is invalid")
        if payload.get("evidence_dir") != str(run_dir):
            raise ValueError("signed validation belongs to a different evidence directory")
        verifier = payload.get("verifier")
        if not isinstance(verifier, dict) or verifier.get("commit") != _verifier_commit(home):
            raise ValueError("signed validation uses a different verifier commit")
        verdict_ref = payload.get("verdict")
        if not isinstance(verdict_ref, dict):
            raise ValueError("signed validation verdict reference is missing")
        if verdict_ref.get("path") != "proofcheck-verdict.json":
            raise ValueError("signed validation verdict path is invalid")
        if verdict_ref.get("sha256") != _hash_file(verdict_path):
            raise ValueError("stored verdict bytes changed after proofcheck")
        if verdict_ref.get("decision") != verdict.get("decision"):
            raise ValueError("stored verdict decision does not match the signed validation")
        artifact_hashes = collect_evidence_artifact_hashes(run_dir)
        if payload.get("artifact_hashes") != artifact_hashes:
            raise ValueError("evidence bytes changed after proofcheck")
        if payload.get("evidence_digest") != _artifact_set_digest(artifact_hashes):
            raise ValueError("evidence digest does not match the signed validation")
        composition = payload.get("composition")
        if not isinstance(composition, dict) or not isinstance(
            composition.get("decision"), str
        ):
            raise ValueError("signed runtime composition is missing")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return _state("unrevalidated", reason=str(exc))

    return {
        "validation_status": "validated",
        "reason": None,
        "verifier_decision": verdict_ref["decision"],
        "effective_decision": composition["decision"],
        "verdict_authorship": "depone",
        "composition_authorship": "witnessd",
        "verifier_commit": verifier["commit"],
        "validation_artifact": str(cache_path),
        "evidence_digest": payload["evidence_digest"],
        "composition": composition,
    }


def _decode_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("payloadType") != VALIDATION_PAYLOAD_TYPE:
        raise ValueError("validation payload type is invalid")
    encoded = envelope.get("payload")
    if not isinstance(encoded, str):
        raise ValueError("validation payload is missing")
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("validation payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("validation payload must be a JSON object")
    return payload


def _state(status: str, *, reason: str) -> dict[str, Any]:
    return {
        "validation_status": status,
        "reason": reason,
        "verifier_decision": None,
        "effective_decision": None,
        "verdict_authorship": None,
        "composition_authorship": None,
    }


def _verifier_commit(home: Path) -> str:
    provision_path = home.resolve(strict=False) / "provision.json"
    try:
        provision = json.loads(provision_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("pinned verifier provision is unreadable") from exc
    depone = provision.get("depone") if isinstance(provision, dict) else None
    if (
        not isinstance(provision, dict)
        or provision.get("kind") != PROVISION_KIND
        or not isinstance(depone, dict)
        or not isinstance(depone.get("commit"), str)
    ):
        raise ValueError("pinned verifier provision is invalid")
    return depone["commit"]


def _artifact_set_digest(artifact_hashes: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical_json(artifact_hashes)).hexdigest()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
