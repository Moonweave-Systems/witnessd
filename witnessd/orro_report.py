"""ORRO human-facing report summary v0.

Reports compress persisted ORRO artifacts into reviewer-facing status. They do
not execute commands, call Depone, rederive verifier truth, approve merge, or
raise assurance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from witnessd.orro_next import decide_next
from witnessd.orro_workflow import (
    role_lane_plan_binding_ref,
    summarize_executable_lanes,
    workflow_plan_binding_ref,
    workflow_role_dispatch_ref,
)


REPORT_KIND = "orro-report"
REPORT_SCHEMA_VERSION = "0.1"

ERR_ORRO_REPORT_ARTIFACT_LOAD_FAILED = "ERR_ORRO_REPORT_ARTIFACT_LOAD_FAILED"
ERR_ORRO_REPORT_WRITE_FAILED = "ERR_ORRO_REPORT_WRITE_FAILED"

DO_NOT_TRUST = [
    "workflow plan alone",
    "role-lane plan alone",
    "role names",
    "session transcript",
    "model confidence",
    "handoff prose as approval",
    "engine-lock as proof",
]


class OrroReportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_report(
    run_dir: Path,
    *,
    home: Path | None = None,
    workstyle_decision: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False) if home is not None else None
    next_code, continuation = decide_next(run_dir, home=home)
    workstyle = _load_workstyle(workstyle_decision) if workstyle_decision is not None else None
    observed = continuation.get("observed_artifacts")
    if not isinstance(observed, dict):
        observed = _observed(run_dir)

    workflow = _workflow_summary(run_dir)
    execution = _execution_summary(run_dir, continuation, observed)
    verification = _verification_summary(run_dir, continuation, observed)
    handoff = _handoff_summary(run_dir, continuation, observed)
    reference_adapter = _reference_adapter_summary(run_dir)
    summary = _summary(continuation, verification, handoff, reference_adapter)
    report = {
        "kind": REPORT_KIND,
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "home": str(home) if home is not None else continuation.get("home"),
        "goal": _goal(workflow, workstyle),
        "summary": summary,
        "workflow": workflow,
        "workstyle": _workstyle_summary(workstyle),
        "execution": execution,
        "verification": verification,
        "handoff": handoff,
        "reference_adapter": reference_adapter,
        "not_real_ai_work": reference_adapter["not_real_ai_work"],
        "placeholder_fallback": reference_adapter["placeholder_fallback"],
        "next": {
            "decision": continuation.get("decision", "blocked"),
            "next_allowed": list(continuation.get("next_allowed", [])),
            "blocked": bool(continuation.get("blocked", next_code != 0)),
            "reasons": list(continuation.get("reasons", [])),
        },
        "auto": _auto_summary(run_dir),
        "human_review": _human_review(summary, workflow, verification, workstyle),
        "do_not_trust": list(DO_NOT_TRUST),
        "boundary": {
            "executes_commands": False,
            "verifies_evidence": False,
            "approves_merge": False,
            "raises_assurance": False,
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
        },
    }
    if isinstance(continuation.get("error"), dict):
        report["error"] = continuation["error"]
    return next_code, report


def write_report(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OrroReportError(ERR_ORRO_REPORT_WRITE_FAILED, str(exc)) from exc


def render_text_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    workflow = payload.get("workflow", {})
    execution = payload.get("execution", {})
    verification = payload.get("verification", {})
    handoff = payload.get("handoff", {})
    human_review = payload.get("human_review", {})
    lines = [
        "ORRO Report",
        f"Goal: {payload.get('goal') or 'unknown'}",
        f"State: {summary.get('state', 'blocked')}",
        f"Profile: {workflow.get('profile') or 'unknown'}",
        _execution_line(execution),
        _verification_line(verification),
        "Handoff: packaged" if handoff.get("handoff_present") else "Handoff: not packaged",
        f"Next: {summary.get('recommended_next_action') or 'none'}",
        "",
        "Do not treat as proof:",
    ]
    lines.extend(f"- {item}" for item in payload.get("do_not_trust", []))
    lines.extend(["", "Human review:"])
    focus = human_review.get("focus")
    if isinstance(focus, list) and focus:
        lines.extend(f"- {item}" for item in focus)
    else:
        lines.append("- no specific reviewer focus recorded")
    return "\n".join(lines) + "\n"


def _execution_line(execution: dict[str, Any]) -> str:
    if not execution.get("proofrun_evidence_present"):
        return "Execution: evidence missing"
    lane_count = execution.get("lane_count", 0)
    adapter_count = execution.get("distinct_adapter_count", 0)
    model_count = execution.get("distinct_model_count", 0)
    if lane_count == 1:
        label = (
            "single-lane policy selection"
            if execution.get("policy_selected")
            else "single-lane execution"
        )
        return (
            f"Execution: evidence present; {label} "
            f"({adapter_count} adapter, {model_count} model)"
        )
    if execution.get("multi_model_execution"):
        return (
            f"Execution: evidence present; multi-model execution across {lane_count} lanes "
            f"({adapter_count} adapters, {model_count} models)"
        )
    return (
        f"Execution: evidence present; {lane_count} lanes "
        f"({adapter_count} adapters, {model_count} models)"
    )


def _verification_line(verification: dict[str, Any]) -> str:
    if verification.get("proofcheck_verdict_present"):
        decision = verification.get("decision") or "unknown"
        return f"Verification: Depone proofcheck {decision}"
    return "Verification: proofcheck missing"


def _summary(
    continuation: dict[str, Any],
    verification: dict[str, Any],
    handoff: dict[str, Any],
    reference_adapter: dict[str, Any],
) -> dict[str, Any]:
    state = str(continuation.get("decision", "blocked"))
    next_allowed = continuation.get("next_allowed")
    next_action = next_allowed[0] if isinstance(next_allowed, list) and next_allowed else None
    return {
        "state": state,
        "headline": _headline(state, verification, handoff, reference_adapter),
        "recommended_next_action": next_action,
        "complete": state == "complete",
        "blocked": bool(continuation.get("blocked", False)),
        "not_real_ai_work": reference_adapter["not_real_ai_work"],
        "placeholder_fallback": reference_adapter["placeholder_fallback"],
    }


def _headline(
    state: str,
    verification: dict[str, Any],
    handoff: dict[str, Any],
    reference_adapter: dict[str, Any],
) -> str:
    if reference_adapter.get("reference_adapter"):
        return (
            "Reference shell adapter evidence exists and proofcheck may pass, "
            "but this is not real AI work."
        )
    if state == "needs-proofcheck":
        return "Execution evidence exists; run proofcheck before handoff."
    if state == "ready-for-handoff":
        return "Execution evidence exists and proofcheck passed; handoff can be packaged."
    if state == "complete":
        return "Passing proofcheck and handoff package are present."
    if state == "invalid-run-dir":
        return "Run directory is missing or invalid."
    if state == "evidence-pending":
        return "ORRO context exists, but execution evidence is missing."
    if verification.get("blocked") or handoff.get("blocked"):
        return "Observed artifacts block continuation."
    return "ORRO report is blocked; inspect reasons before continuing."


def _workflow_summary(run_dir: Path) -> dict[str, Any]:
    workflow_ref = workflow_plan_binding_ref(run_dir)
    role_lane_ref = role_lane_plan_binding_ref(run_dir)
    dispatch_ref = workflow_role_dispatch_ref(run_dir)
    workflow_plan = _load_json_object(run_dir / "workflow-plan.json")
    role_lane_plan = _load_json_object(run_dir / "role-lane-plan.json")
    return {
        "profile": _first_string(
            workflow_ref,
            "profile",
            role_lane_ref,
            "profile",
            workflow_plan,
            "profile",
            role_lane_plan,
            "workflow_profile",
            dispatch_ref,
            "profile",
        ),
        "workflow_plan_present": (run_dir / "workflow-plan.json").is_file(),
        "workflow_plan_hash": _ref_hash(workflow_ref, run_dir / "workflow-plan.json"),
        "workflow_plan": workflow_ref,
        "role_lane_plan_present": (run_dir / "role-lane-plan.json").is_file(),
        "role_lane_plan_hash": _ref_hash(role_lane_ref, run_dir / "role-lane-plan.json"),
        "role_lane_plan": role_lane_ref,
        "role_dispatch_present": (run_dir / "workflow-role-dispatch.json").is_file(),
        "role_dispatch_hash": _ref_hash(dispatch_ref, run_dir / "workflow-role-dispatch.json"),
        "role_dispatch": dispatch_ref,
    }


def _execution_summary(
    run_dir: Path,
    continuation: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    ledger = _load_json_object(run_dir / "team-ledger.json")
    lanes = ledger.get("lanes") if isinstance(ledger, dict) else None
    executed_lanes = lanes if isinstance(lanes, list) else []
    role_lane_plan = _load_json_object(run_dir / "role-lane-plan.json")
    planned_lanes = (
        role_lane_plan.get("lanes") if isinstance(role_lane_plan, dict) else []
    )
    if not isinstance(planned_lanes, list):
        planned_lanes = []
    planned_by_id = {
        str(lane.get("lane_id")): lane
        for lane in planned_lanes
        if isinstance(lane, dict) and lane.get("lane_id") is not None
    }
    summary_lanes = []
    for lane in executed_lanes:
        if not isinstance(lane, dict):
            continue
        planned = planned_by_id.get(str(lane.get("lane_id")), {})
        summary_lanes.append({**lane, **planned})
    execution_summary = summarize_executable_lanes(summary_lanes)
    policy_selected = len(summary_lanes) == 1 and (
        summary_lanes[0].get("model_source") == "model-policy"
    )
    return {
        "proofrun_evidence_present": bool(observed.get("team_ledger")),
        "team_ledger_present": bool(observed.get("team_ledger")),
        "team_ledger_verdict_present": bool(observed.get("team_ledger_verdict")),
        **execution_summary,
        "policy_selected": policy_selected,
        "runner_roles": [
            role
            for role in continuation.get("role_status", [])
            if isinstance(role, dict) and role.get("phase") == "proofrun"
        ],
    }


def _verification_summary(
    run_dir: Path,
    continuation: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    verdict = _load_json_object(run_dir / "proofcheck-verdict.json")
    decision = verdict.get("decision") if isinstance(verdict, dict) else None
    state = str(continuation.get("decision", "blocked"))
    return {
        "proofcheck_verdict_present": bool(observed.get("proofcheck_verdict")),
        "decision": decision,
        "verifier_command": verdict.get("verifier_command") if isinstance(verdict, dict) else None,
        "verified_by": "Depone",
        "blocked": state == "blocked",
        "refuted": decision in {"fail", "refuted"},
        "error": continuation.get("error") if isinstance(continuation.get("error"), dict) else None,
    }


def _handoff_summary(
    run_dir: Path,
    continuation: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    handoff = _load_json_object(run_dir / "orro-handoff.json")
    state = str(continuation.get("decision", "blocked"))
    return {
        "handoff_present": bool(observed.get("handoff")),
        "ready_for_handoff": state == "ready-for-handoff",
        "approves_merge": False,
        "raises_assurance": False,
        "blocked": state == "blocked",
        "artifact": handoff if isinstance(handoff, dict) else None,
    }


def _workstyle_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "decision_present": False,
            "task_class": None,
            "recommended_effort": None,
            "actions_to_skip": [],
        }
    return {
        "decision_present": True,
        "task_class": payload.get("task_class"),
        "recommended_profile": payload.get("recommended_profile"),
        "recommended_effort": payload.get("recommended_effort"),
        "actions_to_skip": payload.get("actions_to_skip", []),
        "human_review_required": payload.get("human_review_required"),
        "boundary": payload.get("boundary", {}),
    }


def _auto_summary(run_dir: Path) -> dict[str, Any]:
    return {
        "plan": _load_json_object(run_dir / "orro-auto-plan.json"),
        "receipt": _load_json_object(run_dir / "orro-auto-receipt.json"),
        "session": _load_json_object(run_dir / "orro-auto-session.json"),
    }


def _reference_adapter_summary(run_dir: Path) -> dict[str, Any]:
    warning = _load_json_object(run_dir / "moonweave-reference-adapter-warning.json")
    if warning is None:
        return {
            "reference_adapter": False,
            "not_real_ai_work": False,
            "placeholder_fallback": False,
            "reference_adapter_lanes": [],
        }
    return {
        "reference_adapter": bool(warning.get("reference_adapter")),
        "not_real_ai_work": bool(warning.get("not_real_ai_work")),
        "placeholder_fallback": bool(warning.get("placeholder_fallback")),
        "reference_adapter_lanes": warning.get("reference_adapter_lanes", []),
        "warning": warning,
        "can_change_evidence_verdict": False,
    }


def _human_review(
    summary: dict[str, Any],
    workflow: dict[str, Any],
    verification: dict[str, Any],
    workstyle: dict[str, Any] | None,
) -> dict[str, Any]:
    focus = ["review changed files", "check proofcheck-verdict.json"]
    if summary.get("state") == "ready-for-handoff":
        focus.append("package handoff before merge")
    if workflow.get("workflow_plan_present"):
        focus.append("confirm workflow plan matches the intended work")
    if workstyle and workstyle.get("task_class") == "risky-change":
        focus.append("perform human review before execution or merge")
    return {
        "required": True,
        "focus": focus,
        "verification_decision": verification.get("decision"),
    }


def _goal(workflow: dict[str, Any], workstyle: dict[str, Any] | None) -> str | None:
    for candidate in (
        workflow.get("workflow_plan"),
        workflow.get("role_lane_plan"),
        workflow.get("role_dispatch"),
        workstyle,
    ):
        if isinstance(candidate, dict) and isinstance(candidate.get("goal"), str):
            return candidate["goal"]
    return None


def _load_workstyle(path: Path) -> dict[str, Any]:
    payload = _load_json_object(path)
    if not isinstance(payload, dict):
        raise OrroReportError(
            ERR_ORRO_REPORT_ARTIFACT_LOAD_FAILED,
            f"failed to read workstyle decision: {path}",
        )
    if payload.get("kind") != "orro-workstyle-decision":
        raise OrroReportError(
            ERR_ORRO_REPORT_ARTIFACT_LOAD_FAILED,
            "workstyle decision must have kind orro-workstyle-decision",
        )
    return payload


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _observed(run_dir: Path) -> dict[str, bool]:
    return {
        "workflow_plan": (run_dir / "workflow-plan.json").is_file(),
        "workflow_plan_binding": (run_dir / "workflow-plan-binding.json").is_file(),
        "role_lane_plan": (run_dir / "role-lane-plan.json").is_file(),
        "role_lane_plan_binding": (run_dir / "role-lane-plan-binding.json").is_file(),
        "workflow_role_dispatch": (run_dir / "workflow-role-dispatch.json").is_file(),
        "team_ledger": (run_dir / "team-ledger.json").is_file(),
        "team_ledger_verdict": (run_dir / "team-ledger-verdict.json").is_file(),
        "proofcheck_verdict": (run_dir / "proofcheck-verdict.json").is_file(),
        "handoff": (run_dir / "orro-handoff.json").is_file(),
    }


def _first_string(*items: Any) -> str | None:
    for payload, key in zip(items[0::2], items[1::2]):
        if isinstance(payload, dict) and isinstance(payload.get(key), str):
            return payload[key]
    return None


def _ref_hash(ref: dict[str, Any] | None, fallback_path: Path) -> str | None:
    if isinstance(ref, dict) and isinstance(ref.get("sha256"), str):
        return ref["sha256"]
    if fallback_path.is_file():
        return _hash_file(fallback_path)
    return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
