#!/usr/bin/env python3
"""Re-derive operator-key archive policy and historical bundle verification."""

from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from depone.agent_fabric.sign import SIGNING_STATUS_OPERATOR_KEY, verify_signed_bundle
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID

ARCHIVE = ROOT / "fixtures" / "key-rotation" / "operator-key-archive.json"

REQUIRED_PRODUCTION_GATE_EVIDENCE = (
    "deployment_record",
    "rotated_key_archive",
    "canary_bundle",
    "depone_verification",
    "operator_review",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fail(message: str) -> None:
    raise AssertionError(message)


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AssertionError(f"{field} must be an ISO-8601 UTC timestamp") from exc


def validate_archive(archive: dict[str, Any]) -> None:
    if archive.get("kind") != "witnessd-operator-key-archive":
        _fail("archive kind mismatch")
    if archive.get("schema_version") != "1.0":
        _fail("archive schema_version mismatch")
    policy = archive.get("policy")
    if not isinstance(policy, dict):
        _fail("archive policy must be an object")
    if policy.get("rotation_interval_days") != 90:
        _fail("rotation interval must be 90 days")
    if "keyless" not in str(policy.get("keyless_gate", "")).lower():
        _fail("keyless gate must be explicit")
    production_gate = archive.get("production_gate")
    if not isinstance(production_gate, dict):
        _fail("production_gate must be an object")
    _validate_production_gate(production_gate)
    keys = archive.get("keys")
    if not isinstance(keys, list) or not keys:
        _fail("archive keys must be a non-empty list")
    current = [key for key in keys if key.get("status") == "current"]
    if len(current) != 1:
        _fail("archive must contain exactly one current key")
    current_key = current[0]
    if current_key.get("key_id") != DEFAULT_OPERATOR_KEY_ID:
        _fail("current key_id must match witnessd runtime default")
    if current_key.get("canary") is not True:
        _fail("current key must have a canary bundle")
    current_from = _parse_utc(current_key.get("valid_from"), "current valid_from")
    if current_key.get("valid_until") is not None:
        _fail("current key must not have valid_until")
    for key in keys:
        _validate_key_record(key, current_key_id=current_key["key_id"], current_from=current_from)


def _validate_production_gate(gate: dict[str, Any]) -> None:
    status_value = gate.get("status")
    if status_value not in {"blocked", "open"}:
        _fail("production gate status must be blocked or open")
    if status_value == "blocked" and "keyless" not in str(gate.get("blocks", "")).lower():
        _fail("blocked production gate must explicitly block keyless")
    if gate.get("rollout_stage") != "external-team-pilot":
        _fail("production gate rollout_stage must be external-team-pilot")
    deployments_min = gate.get("deployments_min")
    if not isinstance(deployments_min, int) or deployments_min < 1:
        _fail("production gate deployments_min must be at least 1")
    required_evidence = gate.get("required_evidence")
    if not isinstance(required_evidence, list):
        _fail("production gate required_evidence must be a list")
    evidence_ids = []
    for item in required_evidence:
        if not isinstance(item, dict):
            _fail("production gate required_evidence entries must be objects")
        evidence_id = item.get("id")
        status = item.get("status")
        if not isinstance(evidence_id, str):
            _fail("production gate evidence id must be a string")
        if status not in {"missing", "recorded"}:
            _fail("production gate evidence status must be missing or recorded")
        if status == "recorded":
            _validate_gate_evidence_artifact(item)
        evidence_ids.append(evidence_id)
    if tuple(evidence_ids) != REQUIRED_PRODUCTION_GATE_EVIDENCE:
        _fail("production gate required_evidence set mismatch")
    if status_value == "open":
        recorded = [item for item in required_evidence if item.get("status") == "recorded"]
        if len(recorded) != len(REQUIRED_PRODUCTION_GATE_EVIDENCE):
            _fail("production gate cannot open without all deployment evidence recorded")


def _validate_gate_evidence_artifact(item: dict[str, Any]) -> None:
    artifact_path = item.get("artifact_path")
    artifact_sha256 = item.get("artifact_sha256")
    if not isinstance(artifact_path, str) or not artifact_path:
        _fail("recorded deployment evidence must include artifact_path")
    if Path(artifact_path).is_absolute():
        _fail("recorded deployment evidence artifact_path must be repo-relative")
    if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64:
        _fail("recorded deployment evidence must include artifact_sha256")
    path = (ROOT / artifact_path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise AssertionError("recorded deployment evidence artifact_path escapes repo") from exc
    if not path.is_file():
        _fail(f"recorded deployment evidence artifact missing: {artifact_path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if artifact_sha256 != actual:
        _fail("recorded deployment evidence artifact_sha256 mismatch")


def _validate_key_record(
    key: dict[str, Any], *, current_key_id: str, current_from: datetime
) -> None:
    key_id = key.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        _fail("key_id must be non-empty")
    status = key.get("status")
    if status not in {"current", "retired", "compromised"}:
        _fail(f"invalid key status: {status!r}")
    valid_from = _parse_utc(key.get("valid_from"), f"{key_id} valid_from")
    if status != "current" and not key.get("valid_until"):
        _fail("retired/compromised key must have valid_until")
    if status != "current":
        valid_until = _parse_utc(key.get("valid_until"), f"{key_id} valid_until")
        if valid_until > current_from:
            _fail("retired/compromised key valid_until must not exceed current valid_from")
        if key.get("rotated_to") != current_key_id:
            _fail("retired/compromised key must link to current rotated_to key_id")
    elif valid_from != current_from:
        _fail("current key valid_from mismatch")

    public_key = ROOT / str(key.get("public_key_path", ""))
    bundle_path = ROOT / str(key.get("bundle_path", ""))
    if not public_key.is_file():
        _fail(f"public key missing: {public_key}")
    if not bundle_path.is_file():
        _fail(f"bundle missing: {bundle_path}")
    bundle = _load(bundle_path)
    if bundle.get("signing_status") != SIGNING_STATUS_OPERATOR_KEY:
        _fail(f"bundle signing_status mismatch: {bundle_path}")
    signatures = bundle.get("dsse_envelope", {}).get("signatures")
    if not isinstance(signatures, list) or not signatures:
        _fail(f"bundle missing signatures: {bundle_path}")
    if len(signatures) != 1 or signatures[0].get("keyid") != key_id:
        _fail(f"archive key_id does not match bundle signature: {bundle_path}")
    if not verify_signed_bundle(bundle, str(public_key)):
        _fail(f"bundle does not verify with archived public key: {bundle_path}")
    predicate = bundle.get("statement", {}).get("predicate", {})
    if status == "current" and predicate.get("source_kind") != "operator-key-rotation-canary":
        _fail("current key bundle must be an operator-key-rotation canary")


def main() -> int:
    validate_archive(_load(ARCHIVE))
    print("key rotation revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
