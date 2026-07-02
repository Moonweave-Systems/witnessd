#!/usr/bin/env python3
"""Re-derive W8 OVERT metadata fixtures from committed bytes via Depone."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.sign import verify_signed_bundle

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "w8"
NEGATIVE = FIX / "negative"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_overt_fields(manifest: dict[str, Any]) -> None:
    _require(
        manifest.get("evidence_mode") == "post_hoc",
        "W8 fixture must declare post_hoc reconstruction",
    )
    _require(
        manifest.get("epoch_seconds") == 300,
        f"epoch_seconds must remain 300, got {manifest.get('epoch_seconds')!r}",
    )
    _require(
        manifest.get("monotonic_counter") == 8,
        f"monotonic_counter must be 8, got {manifest.get('monotonic_counter')!r}",
    )
    parent = manifest.get("parent_attestation_id")
    _require(
        isinstance(parent, str)
        and len(parent) == 64
        and all(c in "0123456789abcdef" for c in parent),
        f"parent_attestation_id must be 64 lowercase hex, got {parent!r}",
    )


def _assert_negative_post_hoc_detected() -> None:
    negative = _load(NEGATIVE / "post_hoc_marked_contemporaneous.json")
    _require(
        negative.get("w8_reconstruction_source") == "post_hoc",
        "negative fixture must identify post_hoc source",
    )
    _require(
        negative.get("evidence_mode") != "post_hoc",
        "negative fixture must be mislabeled to prove the guard",
    )


def main() -> int:
    public_key_path = str(FIX / "keys" / "operator.pub")
    manifest = _load(FIX / "capture-manifest.json")
    _require(
        validate_capture_manifest(manifest) == [],
        "W8 capture-manifest must validate through Depone",
    )
    _assert_overt_fields(manifest)

    receipt = _load(FIX / "runner-receipt.json")
    _require(
        validate_runner_receipt(receipt) == [],
        "W8 runner-receipt must validate through Depone",
    )

    bundle = _load(FIX / "bundle.json")
    _require(
        verify_signed_bundle(bundle, public_key_path),
        "W8 bundle must verify against the operator public key",
    )
    _require(
        bundle.get("evidence_mode") == manifest["evidence_mode"],
        "bundle evidence_mode must mirror the manifest",
    )
    _require(
        bundle.get("epoch_seconds") == manifest["epoch_seconds"],
        "bundle epoch_seconds must mirror the manifest",
    )
    _require(
        bundle.get("monotonic_counter") == manifest["monotonic_counter"],
        "bundle monotonic_counter must mirror the manifest",
    )
    _require(
        bundle.get("parent_attestation_id") == manifest["parent_attestation_id"],
        "bundle parent_attestation_id must mirror the manifest",
    )

    verdict = ingest_signed_evidence_bundle(
        bundle,
        public_key_path,
        {
            "capture-manifest": str(FIX / "capture-manifest.json"),
            "observer-capture": str(FIX / "observer-capture.json"),
            "runner-receipt": str(FIX / "runner-receipt.json"),
        },
        otel_spans=bundle.get("otel_spans"),
    )
    _require(
        verdict.get("signature_verified") is True,
        "W8 bundle ingest must report signature_verified",
    )
    _require(
        verdict.get("decision") == "pass",
        f"W8 bundle ingest must pass, got {verdict!r}",
    )

    _assert_negative_post_hoc_detected()
    print("W8 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
