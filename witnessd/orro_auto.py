"""ORRO non-executing automation planner v0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from witnessd.orro_next import decide_next


AUTO_PLAN_KIND = "orro-auto-plan"
AUTO_PLAN_SCHEMA_VERSION = "0.1"
AUTO_RECEIPT_KIND = "orro-auto-receipt"
AUTO_RECEIPT_SCHEMA_VERSION = "0.1"

ERR_ORRO_AUTO_BLOCKED = "ERR_ORRO_AUTO_BLOCKED"
ERR_ORRO_AUTO_WRITE_FAILED = "ERR_ORRO_AUTO_WRITE_FAILED"


class OrroAutoError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_auto_plan(run_dir: Path, *, home: Path | None = None) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False) if home is not None else None
    next_code, continuation = decide_next(run_dir, home=home)
    decision = str(continuation.get("decision", "blocked"))
    reasons = list(continuation.get("reasons", []))

    payload = _base_plan(
        run_dir,
        continuation=continuation,
        continuation_exit_code=next_code,
        decision=decision,
        blocked=bool(continuation.get("blocked", next_code != 0)),
        reasons=reasons,
    )

    if decision == "needs-proofcheck":
        resolved_home = Path(str(continuation.get("home", home or run_dir.parent.parent)))
        payload["blocked"] = False
        payload["would_run"] = [
            {
                "phase": "proofcheck",
                "command": [
                    "orro",
                    "proofcheck",
                    str(run_dir),
                    "--home",
                    str(resolved_home),
                    "--out",
                    str(run_dir / "proofcheck-verdict.json"),
                ],
                "engine": "Depone",
                "executes_workers": False,
                "verifies_evidence": True,
                "requires_human": False,
            }
        ]
        return 0, payload

    if decision == "ready-for-handoff":
        payload["blocked"] = False
        payload["would_run"] = [
            {
                "phase": "handoff",
                "command": [
                    "orro",
                    "handoff",
                    str(run_dir),
                    "--out",
                    str(run_dir / "orro-handoff.json"),
                ],
                "engine": "ORRO/witnessd",
                "executes_workers": False,
                "verifies_evidence": False,
                "requires_human": False,
            }
        ]
        return 0, payload

    if decision == "complete":
        payload["blocked"] = False
        return 0, payload

    payload["blocked"] = True
    payload["would_run"] = []
    payload["error"] = {
        "code": ERR_ORRO_AUTO_BLOCKED,
        "message": "ORRO auto dry-run is blocked by continuation state",
    }
    if decision == "invalid-run-dir":
        return 2, payload
    return 1, payload


def write_auto_plan(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OrroAutoError(ERR_ORRO_AUTO_WRITE_FAILED, str(exc)) from exc


def build_auto_receipt(
    run_dir: Path,
    *,
    decision_before: str,
    executed: bool,
    executed_phase: str | None,
    command: list[str],
    exit_code: int,
    decision_after: str,
    wrote: list[str],
    reasons: list[str] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": AUTO_RECEIPT_KIND,
        "schema_version": AUTO_RECEIPT_SCHEMA_VERSION,
        "mode": "once",
        "run_dir": str(run_dir.resolve(strict=False)),
        "decision_before": decision_before,
        "executed": executed,
        "executed_phase": executed_phase,
        "command": command,
        "exit_code": exit_code,
        "decision_after": decision_after,
        "wrote": wrote,
        "reasons": reasons or [],
        "boundary": {
            "auto_once": True,
            "executes_at_most_one_step": True,
            "launches_workers": False,
            "executes_proofrun": False,
            "mutates_worktree": False,
            "verifies_evidence_itself": False,
            "delegates_verification_to_depone": executed_phase == "proofcheck",
            "approves_merge": False,
            "raises_assurance": False,
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
        },
    }
    if error is not None:
        payload["error"] = error
    return payload


def write_auto_receipt(path: Path, payload: dict[str, Any]) -> None:
    write_auto_plan(path, payload)


def _base_plan(
    run_dir: Path,
    *,
    continuation: dict[str, Any],
    continuation_exit_code: int,
    decision: str,
    blocked: bool,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "kind": AUTO_PLAN_KIND,
        "schema_version": AUTO_PLAN_SCHEMA_VERSION,
        "mode": "dry-run",
        "run_dir": str(run_dir),
        "decision": decision,
        "continuation_decision": {
            "decision": decision,
            "locked_to": str(run_dir),
            "exit_code": continuation_exit_code,
            "error": continuation.get("error"),
        },
        "would_run": [],
        "blocked": blocked,
        "reasons": reasons,
        "boundary": {
            "dry_run": True,
            "executes_commands": False,
            "launches_workers": False,
            "mutates_worktree": False,
            "verifies_evidence": False,
            "approves_merge": False,
            "raises_assurance": False,
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
        },
    }
