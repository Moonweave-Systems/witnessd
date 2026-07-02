#!/usr/bin/env python3
"""Re-derive operator-key archive policy and historical bundle verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from depone.agent_fabric.sign import SIGNING_STATUS_OPERATOR_KEY, verify_signed_bundle

ARCHIVE = ROOT / "fixtures" / "key-rotation" / "operator-key-archive.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fail(message: str) -> None:
    raise AssertionError(message)


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
    keys = archive.get("keys")
    if not isinstance(keys, list) or not keys:
        _fail("archive keys must be a non-empty list")
    current = [key for key in keys if key.get("status") == "current"]
    if len(current) != 1:
        _fail("archive must contain exactly one current key")
    for key in keys:
        _validate_key_record(key)


def _validate_key_record(key: dict[str, Any]) -> None:
    key_id = key.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        _fail("key_id must be non-empty")
    status = key.get("status")
    if status not in {"current", "retired", "compromised"}:
        _fail(f"invalid key status: {status!r}")
    if status != "current" and not key.get("valid_until"):
        _fail("retired/compromised key must have valid_until")

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
    if signatures[0].get("keyid") != key_id:
        _fail(f"archive key_id does not match bundle signature: {bundle_path}")
    if not verify_signed_bundle(bundle, str(public_key)):
        _fail(f"bundle does not verify with archived public key: {bundle_path}")


def main() -> int:
    validate_archive(_load(ARCHIVE))
    print("key rotation revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
