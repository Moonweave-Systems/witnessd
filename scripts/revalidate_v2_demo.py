#!/usr/bin/env python3
"""Re-derive the v2 one-command team demo fixture from committed bytes."""

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
from witnessd.planner import dispatch, seal_plan, validate_lane_packet  # noqa: E402


FIXTURE_DIR = ROOT / "fixtures" / "v2-demo"
PUBLIC_KEY = FIXTURE_DIR / "keys" / "operator.pub"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_plan_and_dispatch() -> str:
    sealed = _load_json(FIXTURE_DIR / "sealed-plan.json")
    packets = sealed.get("packets")
    _require(isinstance(packets, list) and packets, "sealed plan must contain packets")
    for packet in packets:
        validate_lane_packet(packet)
    recomputed_hash = canonical_hash(packets)
    _require(sealed.get("plan_hash") == recomputed_hash, "plan_hash mismatch")
    _require(
        seal_plan(packets, goal=str(sealed.get("goal"))) == sealed,
        "seal_plan did not reproduce v2 fixture",
    )
    dispatch_events = dispatch(sealed)
    dispatch_log = [
        {
            key: value
            for key, value in record.items()
            if key not in {"seq", "prev_event_hash", "event_hash"}
        }
        for record in _load_jsonl(FIXTURE_DIR / "dispatch-log.jsonl")
    ]
    _require(
        dispatch_log == dispatch_events,
        "dispatch log mismatch",
    )
    return recomputed_hash


def _assert_lane_evidence(ledger: dict[str, Any]) -> None:
    for lane in ledger["lanes"]:
        lane_id = lane["lane_id"]
        lane_dir = FIXTURE_DIR / lane["evidence_dir"]
        manifest = _load_json(lane_dir / "capture-manifest.json")
        manifest_errors = validate_capture_manifest(manifest)
        _require(manifest_errors == [], f"{lane_id} manifest invalid: {manifest_errors}")
        chain = verify_capture_chain([manifest])
        _require(chain["decision"] == "pass", f"{lane_id} capture chain failed: {chain}")

        receipt = _load_json(lane_dir / "runner-receipt.json")
        receipt_errors = validate_runner_receipt(receipt)
        _require(receipt_errors == [], f"{lane_id} receipt invalid: {receipt_errors}")
        _require(receipt["runner_kind"] == "codex-cli", f"{lane_id} must be codex-cli")
        _require(receipt["exit_code"] == 0, f"{lane_id} codex exit code must be 0")
        _require(receipt["touched_files"], f"{lane_id} must have a non-empty diff")

        bundle = _load_json(lane_dir / "bundle.json")
        _require(
            verify_signed_bundle(bundle, str(PUBLIC_KEY)) is True,
            f"{lane_id} signature failed",
        )
        ingest = ingest_signed_evidence_bundle(
            bundle,
            str(PUBLIC_KEY),
            {
                "capture-manifest": str(lane_dir / "capture-manifest.json"),
                "observer-capture": str(lane_dir / "observer-capture.json"),
                "runner-receipt": str(lane_dir / "runner-receipt.json"),
            },
            otel_spans=bundle.get("otel_spans"),
        )
        _require(ingest["decision"] == "pass", f"{lane_id} ingest failed: {ingest}")
        _require(ingest["signature_verified"] is True, f"{lane_id} signature not verified")


def _assert_team_ledger() -> dict[str, Any]:
    ledger = _load_json(FIXTURE_DIR / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIXTURE_DIR)
    _require(verdict["decision"] == "pass", f"team ledger failed: {verdict}")
    _require(verdict["passed_lane_count"] == 1, f"expected one passed lane: {verdict}")
    _require(verdict["blocked_lane_count"] == 0, f"unexpected blocked lane: {verdict}")
    _require(verdict["overlapping_touched_files"] == [], "v2 fixture must not overlap")
    return ledger


def _assert_negative_forged_signature_rejected(ledger: dict[str, Any]) -> None:
    lane_dir = FIXTURE_DIR / ledger["lanes"][0]["evidence_dir"]
    forged = copy.deepcopy(_load_json(lane_dir / "bundle.json"))
    forged["dsse_envelope"]["signatures"][0]["sig"] = "AA=="
    _require(
        verify_signed_bundle(forged, str(PUBLIC_KEY)) is False,
        "forged v2 bundle signature unexpectedly verified",
    )


def _assert_no_credentials() -> None:
    forbidden_names = {"auth.json", "config.toml", "operator-ed25519.pem"}
    for path in FIXTURE_DIR.rglob("*"):
        if path.is_file():
            _require(path.name not in forbidden_names, f"credential-like file in fixture: {path}")
            data = path.read_bytes()
            _require(b"PRIVATE KEY" not in data, f"private key material in fixture: {path}")
            _require(b"sk-" not in data, f"API-key-like token in fixture: {path}")


def main() -> int:
    plan_hash = _assert_plan_and_dispatch()
    ledger = _assert_team_ledger()
    _assert_lane_evidence(ledger)
    _assert_negative_forged_signature_rejected(ledger)
    _assert_no_credentials()
    print(f"v2_demo plan_hash: {plan_hash}")
    print(f"v2_demo lanes: {len(ledger['lanes'])}")
    print("revalidate_v2_demo: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
