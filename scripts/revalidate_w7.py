#!/usr/bin/env python3
"""Re-derive W7 adapter-backed team fan-in fixtures from committed bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import (
    ingest_signed_evidence_bundle,
    verify_capture_chain,
)
from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.sign import verify_signed_bundle
from depone.agent_fabric.team_ledger import build_team_ledger_verdict

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "w7"
NEGATIVE = FIX / "negative"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_team_ledger() -> dict[str, Any]:
    ledger = _load(FIX / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIX)
    _require(verdict["decision"] == "pass", f"W7 team ledger must pass: {verdict}")
    kinds = {lane["lane_id"]: lane["runner_adapter_kind"] for lane in ledger["lanes"]}
    _require(kinds == {"shell-lane": "shell", "codex-lane": "codex"}, f"unexpected lane kinds: {kinds}")
    _require(verdict["overlapping_touched_files"] == [], "W7 fixture must have no overlap")
    return ledger


def _assert_lane_evidence(ledger: dict[str, Any]) -> None:
    pub = str(FIX / "keys" / "operator.pub")
    for lane in ledger["lanes"]:
        lane_dir = FIX / lane["evidence_dir"]
        manifest = _load(lane_dir / "capture-manifest.json")
        errors = validate_capture_manifest(manifest)
        _require(errors == [], f"{lane['lane_id']} manifest invalid: {errors}")
        chain = verify_capture_chain([manifest])
        _require(chain["decision"] == "pass", f"{lane['lane_id']} chain failed: {chain}")

        receipt = _load(lane_dir / "runner-receipt.json")
        receipt_errors = validate_runner_receipt(receipt)
        _require(receipt_errors == [], f"{lane['lane_id']} runner receipt invalid: {receipt_errors}")
        if lane["lane_id"] == "codex-lane":
            _require(receipt["runner_kind"] == "codex-cli", "codex lane must use codex-cli runner_kind")
        if lane["lane_id"] == "shell-lane":
            _require(receipt["runner_kind"] == "manual", "shell lane must use manual runner_kind")

        bundle = _load(lane_dir / "bundle.json")
        _require(verify_signed_bundle(bundle, pub) is True, f"{lane['lane_id']} signature failed")
        ingest = ingest_signed_evidence_bundle(
            bundle,
            pub,
            {
                "capture-manifest": str(lane_dir / "capture-manifest.json"),
                "observer-capture": str(lane_dir / "observer-capture.json"),
                "runner-receipt": str(lane_dir / "runner-receipt.json"),
            },
            otel_spans=bundle.get("otel_spans"),
        )
        _require(ingest["decision"] == "pass", f"{lane['lane_id']} ingest failed: {ingest}")
        _require(ingest["signature_verified"] is True, f"{lane['lane_id']} signature not verified")


def _assert_negative_budget_blocked() -> None:
    ledger = _load(NEGATIVE / "ledger-budget-blocked.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIX)
    _require(
        verdict["decision"] != "pass",
        f"budget-blocked negative unexpectedly passed: {verdict}",
    )
    _require(verdict["blocked_lane_count"] == 1, f"budget negative must block one lane: {verdict}")


def main() -> int:
    ledger = _assert_team_ledger()
    _assert_lane_evidence(ledger)
    _assert_negative_budget_blocked()
    print("W7 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
