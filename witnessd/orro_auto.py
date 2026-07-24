"""ORRO non-executing automation planner v0."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from witnessd.cli.status import _suggested_step_command, build_status
from witnessd.orro_next import decide_next
from witnessd.orro_next import team_ledger_block_diagnostics
from witnessd.orro_roadmap import OrroRoadmapError, read_roadmap


AUTO_PLAN_KIND = "orro-auto-plan"
AUTO_PLAN_SCHEMA_VERSION = "0.1"
AUTO_RECEIPT_KIND = "orro-auto-receipt"
AUTO_RECEIPT_SCHEMA_VERSION = "0.1"
AUTO_SESSION_KIND = "orro-auto-session"
AUTO_SESSION_SCHEMA_VERSION = "0.1"

ERR_ORRO_AUTO_BLOCKED = "ERR_ORRO_AUTO_BLOCKED"
ERR_ORRO_AUTO_WRITE_FAILED = "ERR_ORRO_AUTO_WRITE_FAILED"
ERR_ORRO_AUTO_STEP_NOT_EXECUTABLE = "ERR_ORRO_AUTO_STEP_NOT_EXECUTABLE"
ERR_ORRO_AUTO_STEP_EVIDENCE_PENDING = "ERR_ORRO_AUTO_STEP_EVIDENCE_PENDING"
ERR_ORRO_AUTO_MAX_STEPS_REACHED = "ERR_ORRO_AUTO_MAX_STEPS_REACHED"


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
    for key, value in continuation.items():
        if key not in payload and key not in {"kind", "schema_version"}:
            payload[key] = value

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
                    "--home",
                    str(continuation.get("home", home or run_dir.parent.parent)),
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
        if continuation.get("ship_ready"):
            payload["next_allowed"] = [str(continuation["ship_command"])]
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


def build_auto_session(
    run_dir: Path,
    *,
    max_steps: int,
    steps: list[dict[str, Any]],
    decision_initial: str,
    decision_final: str,
    complete: bool,
    blocked: bool,
    reasons: list[str] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delegates_verification = any(step.get("executed_phase") == "proofcheck" for step in steps)
    payload: dict[str, Any] = {
        "kind": AUTO_SESSION_KIND,
        "schema_version": AUTO_SESSION_SCHEMA_VERSION,
        "mode": "until-complete",
        "run_dir": str(run_dir.resolve(strict=False)),
        "max_steps": max_steps,
        "steps_executed": len(steps),
        "decision_initial": decision_initial,
        "decision_final": decision_final,
        "complete": complete,
        "blocked": blocked,
        "reasons": reasons or [],
        "steps": steps,
        "boundary": {
            "auto_until_complete": True,
            "bounded": True,
            "max_steps_enforced": True,
            "launches_workers": False,
            "executes_proofrun": False,
            "mutates_worktree": False,
            "verifies_evidence_itself": False,
            "delegates_verification_to_depone": delegates_verification,
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


def write_auto_session(path: Path, payload: dict[str, Any]) -> None:
    write_auto_plan(path, payload)


def run_item_session(
    *, repo: Path, home: Path, item_id: str, max_steps: int
) -> tuple[int, dict[str, Any]]:
    """Execute the declared next item steps, stopping at the first evidence block."""
    repo = repo.resolve(strict=False)
    home = home.resolve(strict=False)
    try:
        roadmap = read_roadmap(repo)
    except OrroRoadmapError as exc:
        return 2, _run_item_error_session(home, item_id, exc.code, str(exc))
    item = next(
        (candidate for candidate in (roadmap or {"items": []}).get("items", [])
         if candidate.get("id") == item_id),
        None,
    )
    if item is None:
        return 2, _run_item_error_session(
            home, item_id, "ERR_ORRO_ROADMAP_ITEM_UNKNOWN", f"unknown roadmap item: {item_id}"
        )
    if not isinstance(item.get("steps"), list):
        return 1, _run_item_error_session(
            home, item_id, ERR_ORRO_AUTO_STEP_NOT_EXECUTABLE,
            "roadmap item does not declare executable steps",
        )

    steps: list[dict[str, Any]] = []
    initial_status = _item_status(build_status(repo=repo, home=home), item_id)
    decision_initial = str(initial_status.get("status", "not-started"))
    current_status = initial_status
    error: dict[str, Any] | None = None
    reasons: list[str] = []

    while len(steps) < max_steps:
        next_step = _next_raw_step(item, current_status)
        if next_step is None:
            break
        command = _suggested_step_command(item, next_step, repo=str(repo))
        if command.startswith("construct the command manually"):
            error = {
                "code": ERR_ORRO_AUTO_STEP_NOT_EXECUTABLE,
                "message": command,
            }
            break
        if command.startswith("orro check "):
            run_dir = home / "runs" / f"auto-{len(steps) + 1}-{next_step['id']}"
            command += f" --run-dir {shlex.quote(str(run_dir))}"
        command += f" --home {shlex.quote(str(home))} --json"
        command_argv = shlex.split(command)
        execution_argv = command_argv
        if command_argv and command_argv[0] == "orro":
            execution_argv = [sys.executable, "-m", "orro", *command_argv[1:]]
        env = os.environ.copy()
        repo_root = str(Path(__file__).resolve().parents[1])
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            repo_root
            if not current_pythonpath
            else f"{repo_root}{os.pathsep}{current_pythonpath}"
        )
        completed = subprocess.run(
            execution_argv,
            cwd=str(repo),
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        run_dir = _produced_run_dir(
            completed.stdout, command_argv, home=home
        )
        current_status = _item_status(build_status(repo=repo, home=home), item_id)
        resulting_step = _status_step(current_status, str(next_step["id"]))
        resulting_state = str(resulting_step.get("state", "not-started"))
        record: dict[str, Any] = {
            "step_id": next_step["id"],
            "command": command,
            "exit_code": completed.returncode,
            "resulting_state": resulting_state,
        }
        if run_dir is not None:
            record["run_dir"] = str(run_dir)
        steps.append(record)
        if resulting_state != "done (verified)":
            blockers = team_ledger_block_diagnostics(run_dir) if run_dir else None
            if blockers:
                reasons.extend(
                    "lane "
                    f"{lane.get('lane_id', 'unknown')} blocked — "
                    f"{lane.get('blocked_reason') or 'no runtime reason reported'}"
                    for lane in blockers.get("blocked_lanes", [])
                    if isinstance(lane, dict)
                )
                record["blockers"] = blockers
            elif completed.stderr.strip():
                reasons.append(completed.stderr.strip())
            error = {
                "code": ERR_ORRO_AUTO_STEP_EVIDENCE_PENDING,
                "message": f"step {next_step['id']} stopped at {resulting_state}",
            }
            break

    complete = _next_raw_step(item, current_status) is None and error is None
    if not complete and error is None:
        error = {
            "code": ERR_ORRO_AUTO_MAX_STEPS_REACHED,
            "message": "max steps reached before item completion",
        }
        reasons.append("max steps reached before item completion")
    decision_final = str(current_status.get("status", "not-started"))
    payload = {
        "kind": AUTO_SESSION_KIND,
        "schema_version": AUTO_SESSION_SCHEMA_VERSION,
        "mode": "run-item",
        "run_dir": str(home),
        "item_id": item_id,
        "max_steps": max_steps,
        "steps_executed": len(steps),
        "decision_initial": decision_initial,
        "decision_final": decision_final,
        "complete": complete,
        "blocked": not complete,
        "reasons": reasons,
        "steps": steps,
        "boundary": {
            "auto_run_item": True,
            "bounded": True,
            "max_steps_enforced": True,
            "launches_workers": True,
            "executes_proofrun": True,
            "mutates_worktree": True,
            "verifies_evidence_itself": False,
            "delegates_verification_to_depone": True,
            "approves_merge": False,
            "raises_assurance": False,
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
        },
    }
    if error is not None:
        payload["error"] = error
    return (0 if complete else 1), payload


def _run_item_error_session(
    home: Path, item_id: str, code: str, message: str
) -> dict[str, Any]:
    return {
        "kind": AUTO_SESSION_KIND,
        "schema_version": AUTO_SESSION_SCHEMA_VERSION,
        "mode": "run-item",
        "run_dir": str(home),
        "item_id": item_id,
        "max_steps": 0,
        "steps_executed": 0,
        "decision_initial": "blocked",
        "decision_final": "blocked",
        "complete": False,
        "blocked": True,
        "reasons": [],
        "steps": [],
        "boundary": {
            "auto_run_item": True,
            "bounded": True,
            "max_steps_enforced": True,
            "launches_workers": True,
            "executes_proofrun": True,
        },
        "error": {"code": code, "message": message},
    }


def _item_status(status: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(
        (item for item in status.get("items", []) if item.get("id") == item_id),
        {"id": item_id, "status": "not-started", "steps": []},
    )


def _status_step(status: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(
        (step for step in status.get("steps", []) if step.get("id") == step_id),
        {"id": step_id, "state": "not-started"},
    )


def _next_raw_step(item: dict[str, Any], status: dict[str, Any]) -> dict[str, Any] | None:
    verified = {
        str(step.get("id"))
        for step in status.get("steps", [])
        if step.get("state") == "done (verified)"
    }
    return next(
        (step for step in item.get("steps", []) if str(step.get("id")) not in verified),
        None,
    )


def _produced_run_dir(stdout: str, command: list[str], *, home: Path) -> Path | None:
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("run_dir"), str):
        return Path(payload["run_dir"]).resolve(strict=False)
    if "--run-dir" in command:
        index = command.index("--run-dir")
        if index + 1 < len(command):
            return Path(command[index + 1]).resolve(strict=False)
    return None




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
        "observed_artifacts": continuation.get("observed_artifacts", {}),
        "next_allowed": list(continuation.get("next_allowed", [])),
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
