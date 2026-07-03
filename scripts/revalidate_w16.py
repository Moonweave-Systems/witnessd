#!/usr/bin/env python3
"""Re-derive the W16 merge-lane fixture from committed bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from depone.agent_fabric.team_ledger import build_team_ledger_verdict  # noqa: E402
from depone.agent_fabric.team_merge_attempt import (  # noqa: E402
    validate_team_merge_attempt_receipt,
)


FIXTURE_DIR = ROOT / "fixtures" / "w16"
SOURCE_LANES = ["lane-a", "lane-b"]
MERGE_LANE = "merge-ab"
SHARED_FILE = "pkg/shared.py"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_pass_with_merge() -> dict[str, Any]:
    ledger = _load_json(FIXTURE_DIR / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIXTURE_DIR)
    _require(verdict["decision"] == "pass", f"W16 ledger must pass: {verdict}")
    _require(
        ledger.get("merge_receipt") == f"{MERGE_LANE}/team-merge-attempt-receipt.json",
        "W16 ledger must link the merge-attempt receipt",
    )
    overlaps = verdict["overlapping_touched_files"]
    _require(len(overlaps) == 1, f"W16 must have exactly one source overlap: {overlaps}")
    _require(overlaps[0]["path"] == SHARED_FILE, f"W16 overlap path drifted: {overlaps}")
    _require(overlaps[0]["lane_ids"] == SOURCE_LANES, f"W16 overlap lanes drifted: {overlaps}")

    lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
    _require(lanes[MERGE_LANE]["touched_files"] == [f"merge/{MERGE_LANE}.txt"], "merge lane must not touch the shared source file")

    receipt = _load_json(FIXTURE_DIR / ledger["merge_receipt"])
    _require(validate_team_merge_attempt_receipt(receipt) == [], "merge attempt receipt producer validation failed")
    _require(receipt["decision"] == "pass", f"merge receipt must pass: {receipt}")
    _require(receipt["exit_code"] == 0, f"merge receipt exit_code drifted: {receipt}")
    _require(receipt["conflict_files"] == [], f"merge receipt must have no conflicts: {receipt}")
    _require(set(overlaps[0]["lane_end_commits"]).issubset(set(receipt["head_commits"])), "merge receipt must cover source end commits")
    _require(SHARED_FILE in receipt["merged_files"], "merge receipt must cover shared file")
    return ledger


def _assert_schedule_order(ledger: dict[str, Any]) -> None:
    schedule = _load_json(FIXTURE_DIR / ledger["schedule_receipt"])
    lanes = {lane["lane_id"]: lane for lane in schedule["lanes"]}
    _require(set(lanes) == {"lane-a", "lane-b", MERGE_LANE}, f"schedule lane ids drifted: {lanes}")
    for source in SOURCE_LANES:
        _require(
            lanes[MERGE_LANE]["spawned_monotonic_ns"] >= lanes[source]["exited_monotonic_ns"],
            f"merge lane must start after {source} exits",
        )
    _require(
        lanes["lane-a"]["spawned_monotonic_ns"] < lanes["lane-b"]["exited_monotonic_ns"]
        and lanes["lane-b"]["spawned_monotonic_ns"] < lanes["lane-a"]["exited_monotonic_ns"],
        "source lanes must overlap to prove W16 parallel source execution",
    )


def _assert_negative() -> None:
    forged = _load_json(FIXTURE_DIR / "negative" / "forged-team-ledger.json")
    verdict = build_team_ledger_verdict(forged, base_dir=FIXTURE_DIR)
    codes = {error["code"] for error in verdict["errors"]}
    _require(verdict["decision"] == "blocked", f"forged merge receipt must block: {verdict}")
    _require(
        "ERR_TEAM_LEDGER_MERGE_RECEIPT_COVERAGE_MISSING" in codes,
        f"forged merge receipt must fail coverage, got {codes}",
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
    ledger = _assert_pass_with_merge()
    _assert_schedule_order(ledger)
    _assert_negative()
    _assert_quota_free()
    print("w16 overlap: lane-a,lane-b -> pkg/shared.py")
    print("w16 merge lane: merge-ab after sources")
    print("w16 negative: forged merge receipt rejected")
    print("revalidate_w16: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
