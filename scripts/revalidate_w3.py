"""G2 — re-derive W3 team fan-in verdicts from committed bytes via Depone."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain
from depone.agent_fabric.team_ledger import build_team_ledger_verdict
from depone.agent_fabric.worktree_receipt import (
    WORKTREE_LANE_RECEIPT_KIND,
    WORKTREE_LANE_RECEIPT_SCHEMA_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "fixtures" / "w3"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _verdict(name: str) -> dict:
    return build_team_ledger_verdict(_load_json(BASE / name), base_dir=BASE)


def _revalidate_team_ledgers() -> dict:
    disjoint = _load_json(BASE / "team-ledger.json")
    verdict = build_team_ledger_verdict(disjoint, base_dir=BASE)
    _require(verdict["decision"] == "pass", f"disjoint must pass: {verdict}")
    _require(
        verdict["overlapping_touched_files"] == [],
        f"disjoint must have no overlap: {verdict}",
    )
    _require(verdict["boundary"]["raises_assurance"] is False, "no assurance raise")
    _require(verdict["boundary"]["approves_merge"] is False, "no merge approval")

    overlap = _verdict("team-ledger-overlap.json")
    _require(overlap["decision"] == "blocked", f"overlap must block: {overlap}")
    overlap_codes = {error["code"] for error in overlap["errors"]}
    _require(
        "ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED" in overlap_codes,
        f"overlap must require merge receipt: {overlap_codes}",
    )

    merged = _verdict("team-ledger-merged.json")
    _require(merged["decision"] == "pass", f"merged must pass: {merged}")
    return disjoint


def _revalidate_receipts(disjoint: dict) -> None:
    for lane in disjoint["lanes"]:
        receipt = _load_json(BASE / lane["worktree_receipt"])
        _require(
            receipt["kind"] == WORKTREE_LANE_RECEIPT_KIND,
            f"{lane['lane_id']} receipt kind drifted",
        )
        _require(
            receipt["schema_version"] == WORKTREE_LANE_RECEIPT_SCHEMA_VERSION,
            f"{lane['lane_id']} receipt schema drifted",
        )
        _require(receipt["dirty"] is False, f"{lane['lane_id']} receipt dirty")
        _require(
            sorted(receipt["changed_files"]) == sorted(lane["touched_files"]),
            f"{lane['lane_id']} changed_files must equal touched_files",
        )


def _revalidate_lane_manifests(disjoint: dict) -> None:
    for lane in disjoint["lanes"]:
        manifest = _load_json(BASE / lane["evidence_dir"] / "capture-manifest.json")
        errors = validate_capture_manifest(manifest)
        _require(errors == [], f"{lane['lane_id']} manifest invalid: {errors}")
        _require(
            manifest["assurance"] == "A2-isolated-observed",
            f"{lane['lane_id']} must preserve W2 A2, got {manifest['assurance']!r}",
        )
        chain = verify_capture_chain([manifest])
        _require(
            chain["decision"] == "pass",
            f"{lane['lane_id']} one-manifest chain must pass: {chain}",
        )


def _revalidate_claim_conflict() -> None:
    events = [
        json.loads(line)
        for line in (BASE / "claim-conflict.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _require(
        any(event["event"] == "claim-conflict" for event in events),
        "claim-conflict runlog must contain claim-conflict event",
    )


def main() -> int:
    disjoint = _revalidate_team_ledgers()
    _revalidate_receipts(disjoint)
    _revalidate_lane_manifests(disjoint)
    _revalidate_claim_conflict()
    print("W3 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
