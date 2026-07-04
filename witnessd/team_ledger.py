"""W3 Team Ledger artifact builders."""

from __future__ import annotations

from typing import Any

TEAM_LEDGER_KIND = "depone-team-ledger"
TEAM_LEDGER_SCHEMA_VERSION = "0.1"


def build_evidence_next_verdict(
    *, blocking_reasons: list[str] | None = None
) -> dict[str, object]:
    reasons = list(blocking_reasons or [])
    return {
        "command": "evidence-next",
        "decision": "blocked" if reasons else "continue",
        "blocking_reasons": reasons,
    }


def classify_lane_kind(*, touched_files: list[Any]) -> str:
    touched = [item for item in touched_files if isinstance(item, str) and item]
    return "write" if touched else "read-only"


def build_team_ledger(
    *,
    leader_objective: str,
    leader_id: str,
    start_commit: str,
    stop_rule: str,
    lanes: list[dict[str, Any]],
    merge_receipt: str | None = None,
    resume_receipt: str | None = None,
    schedule_receipt: str | None = None,
) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "kind": TEAM_LEDGER_KIND,
        "schema_version": TEAM_LEDGER_SCHEMA_VERSION,
        "leader_objective": leader_objective,
        "leader_id": leader_id,
        "start_commit": start_commit,
        "stop_rule": stop_rule,
        "lanes": list(lanes),
    }
    if merge_receipt is not None:
        ledger["merge_receipt"] = merge_receipt
    if resume_receipt is not None:
        ledger["resume_receipt"] = resume_receipt
    if schedule_receipt is not None:
        ledger["schedule_receipt"] = schedule_receipt
    return ledger


def build_team_ledger_merge_receipt(
    *,
    lanes: list[str],
    files: list[str],
    conflict_events: list[Any] | None = None,
    decision: str = "pass",
) -> dict[str, Any]:
    if decision not in {"pass", "blocked"}:
        raise ValueError("ERR_TEAM_LEDGER_MERGE_RECEIPT_DECISION_INVALID")
    return {
        "command": "team-ledger-merge-receipt",
        "schema_version": "1.0",
        "decision": decision,
        "lanes": sorted(set(lanes)),
        "files": sorted(set(files)),
        "conflict_events": list(conflict_events or []),
    }


def _self_test() -> None:
    verdict = build_evidence_next_verdict()
    assert verdict["command"] == "evidence-next"
    assert verdict["decision"] == "continue"
    assert classify_lane_kind(touched_files=[]) == "read-only"
    assert build_team_ledger(
        leader_objective="x",
        leader_id="leader",
        start_commit="abc",
        stop_rule="done",
        lanes=[{"lane_id": "lane-a"}],
    )["kind"] == TEAM_LEDGER_KIND
