#!/usr/bin/env python3
"""Re-derive the W13 team-run codex auth/region fixture from committed bytes."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest  # noqa: E402
from depone.agent_fabric.evidence_substrate import (  # noqa: E402
    ingest_signed_evidence_bundle,
    verify_capture_chain,
)
from depone.agent_fabric.paired_run import validate_runner_receipt  # noqa: E402
from depone.agent_fabric.sign import verify_signed_bundle  # noqa: E402
from depone.agent_fabric.team_ledger import build_team_ledger_verdict  # noqa: E402
from witnessd.canonical import canonical_hash  # noqa: E402


FIXTURE_DIR = ROOT / "fixtures" / "w13"
LANE_DIR = FIXTURE_DIR / "impl"
PUBLIC_KEY = FIXTURE_DIR / "keys" / "operator.pub"
EXPECTED_REGION = ["pkg/w13_marker.py"]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_team_ledger() -> dict[str, Any]:
    ledger = _load_json(FIXTURE_DIR / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIXTURE_DIR)
    _require(verdict["decision"] == "pass", f"W13 team ledger failed: {verdict}")
    _require(verdict["passed_lane_count"] == 1, f"expected one passed lane: {verdict}")
    _require(verdict["blocked_lane_count"] == 0, f"unexpected blocked lane: {verdict}")
    _require(verdict["overlapping_touched_files"] == [], "W13 fixture must not overlap")
    lane = ledger["lanes"][0]
    _require(lane["runner_adapter_kind"] == "codex", "W13 lane must be codex")
    _require(lane["team_adapter_kind"] == "codex", "W13 team adapter kind must be codex")
    _require(lane["touched_files"] == EXPECTED_REGION, f"W13 touched files mismatch: {lane}")
    return ledger


def _assert_lane_evidence() -> dict[str, Any]:
    manifest = _load_json(LANE_DIR / "capture-manifest.json")
    errors = validate_capture_manifest(manifest)
    _require(errors == [], f"W13 capture manifest invalid: {errors}")
    _require(
        verify_capture_chain([manifest])["decision"] == "pass",
        "W13 capture chain must pass",
    )
    _require(
        manifest["allowed_touched_files"] == EXPECTED_REGION,
        f"W13 allowed_touched_files must bind the declared region: {manifest['allowed_touched_files']}",
    )
    _require(
        manifest["observer_capture"]["touched_files"] == EXPECTED_REGION,
        f"W13 observer touched files mismatch: {manifest['observer_capture']['touched_files']}",
    )

    receipt = _load_json(LANE_DIR / "runner-receipt.json")
    receipt_errors = validate_runner_receipt(receipt)
    _require(receipt_errors == [], f"W13 runner receipt invalid: {receipt_errors}")
    _require(receipt["runner_kind"] == "codex-cli", "W13 runner_kind must be codex-cli")
    _require(receipt["exit_code"] == 0, f"W13 codex exit must be 0: {receipt}")
    _require(receipt["touched_files"] == EXPECTED_REGION, "W13 receipt touched files mismatch")
    _require("exec" in receipt["invocation"], "W13 codex invocation must use exec")

    bundle = _load_json(LANE_DIR / "bundle.json")
    _require(verify_signed_bundle(bundle, str(PUBLIC_KEY)) is True, "W13 signature failed")
    ingest = ingest_signed_evidence_bundle(
        bundle,
        str(PUBLIC_KEY),
        {
            "capture-manifest": str(LANE_DIR / "capture-manifest.json"),
            "observer-capture": str(LANE_DIR / "observer-capture.json"),
            "runner-receipt": str(LANE_DIR / "runner-receipt.json"),
        },
        otel_spans=bundle.get("otel_spans"),
    )
    _require(ingest["decision"] == "pass", f"W13 bundle ingest failed: {ingest}")
    _require(ingest["signature_verified"] is True, "W13 signature not verified")
    return manifest


def _assert_out_of_region_negative(manifest: dict[str, Any]) -> None:
    forged = copy.deepcopy(manifest)
    forged["observer_capture"]["touched_files"] = EXPECTED_REGION + ["pkg/outside.py"]
    forged["observer_capture_hash"] = canonical_hash(forged["observer_capture"])
    errors = validate_capture_manifest(forged)
    _require(
        any("unexpected touched files" in error for error in errors),
        f"W13 out-of-region touched file must be blocked: {errors}",
    )


def _assert_no_credentials() -> None:
    forbidden_names = {"auth.json", "config.toml", "operator-ed25519.pem"}
    for path in FIXTURE_DIR.rglob("*"):
        if not path.is_file():
            continue
        _require(path.name not in forbidden_names, f"credential-like file in fixture: {path}")
        data = path.read_bytes()
        _require(b"PRIVATE KEY" not in data, f"private key material in fixture: {path}")
        _require(b"subscription-w13" not in data, f"codex auth secret in fixture: {path}")


def main() -> int:
    ledger = _assert_team_ledger()
    manifest = _assert_lane_evidence()
    _assert_out_of_region_negative(manifest)
    _assert_no_credentials()
    print(f"w13 lanes: {len(ledger['lanes'])}")
    print("w13 runner_kind: codex-cli")
    print("w13 region: pkg/w13_marker.py")
    print("revalidate_w13: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
