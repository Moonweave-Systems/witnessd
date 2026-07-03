#!/usr/bin/env python3
"""Re-derive the W15 parallel execution fixture from committed bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest  # noqa: E402
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle  # noqa: E402
from depone.agent_fabric.paired_run import validate_runner_receipt  # noqa: E402
from depone.agent_fabric.sign import verify_signed_bundle  # noqa: E402
from depone.agent_fabric.team_ledger import (  # noqa: E402
    build_team_ledger_verdict,
    validate_team_schedule_receipt,
)
from witnessd.runlog import verify_runlog  # noqa: E402


FIXTURE_DIR = ROOT / "fixtures" / "w15"
PUBLIC_KEY = FIXTURE_DIR / "keys" / "operator.pub"
EXPECTED_LANES = ["lane-a", "lane-b", "lane-c"]
EXPECTED_FILES = {
    "lane-a": ["pkg/a.py"],
    "lane-b": ["pkg/b.py"],
    "lane-c": ["pkg/c.py"],
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_team_schedule() -> dict[str, Any]:
    ledger = _load_json(FIXTURE_DIR / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIXTURE_DIR)
    _require(verdict["decision"] == "pass", f"W15 ledger must pass: {verdict}")
    _require(verdict["overlapping_touched_files"] == [], "W15 regions must be disjoint")
    _require(
        verdict["schedule_receipt"]["lane_ids"] == EXPECTED_LANES,
        f"W15 schedule lane coverage drifted: {verdict['schedule_receipt']}",
    )
    _require(
        verdict["schedule_receipt"]["derived_max_overlap"] >= 2,
        f"W15 must prove process overlap >=2: {verdict['schedule_receipt']}",
    )

    receipt = _load_json(FIXTURE_DIR / ledger["schedule_receipt"])
    _require(validate_team_schedule_receipt(receipt) == [], "schedule receipt invalid")
    _require(
        receipt["boundary"]["note"]
        == "single-host orchestrator clock process-concurrency basis",
        "schedule receipt boundary note drifted",
    )
    _require(
        all(lane["exit_code"] == 0 for lane in receipt["lanes"]),
        "W15 pass fixture lanes must all exit 0",
    )

    bundle = _load_json(FIXTURE_DIR / "team-schedule-receipt-bundle.json")
    _require(verify_signed_bundle(bundle, str(PUBLIC_KEY)) is True, "schedule signature failed")
    ingest = ingest_signed_evidence_bundle(
        bundle,
        str(PUBLIC_KEY),
        {"team-schedule-receipt": str(FIXTURE_DIR / ledger["schedule_receipt"])},
        otel_spans=bundle.get("otel_spans"),
    )
    _require(ingest["decision"] == "pass", f"schedule bundle ingest failed: {ingest}")
    return ledger


def _assert_lane_evidence(ledger: dict[str, Any]) -> None:
    for lane in ledger["lanes"]:
        lane_id = lane["lane_id"]
        _require(lane["verification_state"] == "pass", f"{lane_id} not pass")
        _require(lane["touched_files"] == EXPECTED_FILES[lane_id], f"{lane_id} files drifted")
        lane_dir = FIXTURE_DIR / lane_id

        manifest = _load_json(lane_dir / "capture-manifest.json")
        _require(validate_capture_manifest(manifest) == [], f"{lane_id} manifest invalid")
        _require(
            manifest["allowed_touched_files"] == EXPECTED_FILES[lane_id],
            f"{lane_id} region binding drifted",
        )

        receipt = _load_json(lane_dir / "runner-receipt.json")
        _require(validate_runner_receipt(receipt) == [], f"{lane_id} runner receipt invalid")
        _require(receipt["runner_kind"] == "manual", f"{lane_id} must be shell/manual")
        _require(receipt["exit_code"] == 0, f"{lane_id} shell command failed")


def _assert_runlog() -> None:
    _require(verify_runlog(_jsonl(FIXTURE_DIR / "runlog.jsonl"))["ok"], "team runlog broken")


def _assert_negative() -> None:
    forged = _load_json(FIXTURE_DIR / "negative" / "forged-impossible-interval.json")
    errors = validate_team_schedule_receipt(forged)
    _require(
        "ERR_TEAM_SCHEDULE_RECEIPT_INTERVAL_INVALID"
        in {error["code"] for error in errors},
        f"forged interval must be rejected: {errors}",
    )


def _assert_quota_free() -> None:
    forbidden = {b"auth.json", b"PRIVATE KEY", b"codex exec", b"claude", b"opencode"}
    for path in FIXTURE_DIR.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for token in forbidden:
            _require(token not in data, f"quota/secret marker {token!r} in {path}")


def main() -> int:
    ledger = _assert_team_schedule()
    _assert_lane_evidence(ledger)
    _assert_runlog()
    _assert_negative()
    _assert_quota_free()
    print("w15 lanes: 3")
    print("w15 derived_max_overlap: >=2")
    print("w15 negative: forged schedule interval rejected")
    print("revalidate_w15: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
