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

from witnessd.cli._output import _hash_file as _output_hash_file
from witnessd.orro_next import decide_next, team_ledger_block_diagnostics
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
    "model-declaration, write-scope-declaration, skill-routing-declaration, and tool-declaration as verifier-re-derived claims",
]

DECLARATION_ARTIFACTS = (
    "model-declaration.json",
    "write-scope-declaration.json",
    "skill-routing-declaration.json",
    "tool-declaration.json",
)


class OrroReportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_report(
    run_dir: Path,
    *,
    home: Path | None = None,
    workstyle_decision: Path | None = None,
    declared_intent: dict[str, Any] | None = None,
    declared_intent_source: Path | None = None,
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
    identity = _identity_summary(run_dir)
    intent = _intent_summary(run_dir, workflow)
    evidence = _evidence_summary(run_dir)
    reference_adapter = _reference_adapter_summary(run_dir)
    summary = _summary(
        continuation, execution, verification, handoff, reference_adapter
    )
    intent_reference = None
    if declared_intent is not None and declared_intent_source is not None:
        from witnessd.orro_intent import declared_intent_ref

        intent_reference = declared_intent_ref(declared_intent_source)
    elif declared_intent is None:
        picked_up = _verified_declared_intent(run_dir)
        if picked_up is not None:
            declared_intent, intent_reference = picked_up
    report = {
        "kind": REPORT_KIND,
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "home": str(home) if home is not None else continuation.get("home"),
        "goal": _goal(workflow, workstyle),
        "summary": summary,
        "workflow": workflow,
        "identity": identity,
        "intent": intent,
        "workstyle": _workstyle_summary(workstyle),
        "execution": execution,
        "verification": verification,
        "declarations": _declaration_summary(run_dir),
        "evidence": evidence,
        "handoff": handoff,
        "reference_adapter": reference_adapter,
        "not_real_ai_work": reference_adapter["not_real_ai_work"],
        "placeholder_fallback": reference_adapter["placeholder_fallback"],
        "next": {
            "decision": continuation.get("decision", "blocked"),
            "next_allowed": list(continuation.get("next_allowed", [])),
            "blocked": bool(continuation.get("blocked", next_code != 0)),
            "reasons": list(continuation.get("reasons", [])),
            **(
                {"diagnostic_command": continuation["diagnostic_command"]}
                if isinstance(continuation.get("diagnostic_command"), str)
                else {}
            ),
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
    if declared_intent is not None and intent_reference is not None:
        report["declared_intent"] = declared_intent
        report["declared_intent_ref"] = intent_reference
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
    identity = payload.get("identity", {})
    intent = payload.get("intent", {})
    evidence = payload.get("evidence", {})
    lines = ["ORRO Report", "Identity:"]
    identity_lanes = identity.get("lanes", []) if isinstance(identity, dict) else []
    if identity_lanes:
        for lane in identity_lanes:
            if not isinstance(lane, dict):
                continue
            lines.append(
                "  "
                + "; ".join(
                    f"{label}: {_display_value(lane.get(key))}"
                    for label, key in (
                        ("lane", "lane_id"),
                        ("base commit", "base_commit"),
                        ("working tree", "working_tree"),
                        ("branch", "branch"),
                        ("worktree", "worktree"),
                        ("task id", "task_id"),
                    )
                    if lane.get(key) is not None
                )
            )
    else:
        lines.append("  no identity observation recorded")

    lines.append("Intent:")
    lines.append(f"  Goal: {_display_value(payload.get('goal'))}")
    lines.append(f"  profile: {_display_value(intent.get('profile') or workflow.get('profile'))}")
    declared_intent = payload.get("declared_intent")
    if isinstance(declared_intent, dict):
        lines.append(f"  Declared intent: {declared_intent.get('intent', 'not recorded')}")
        non_goals = declared_intent.get("non_goals")
        if isinstance(non_goals, list) and non_goals:
            lines.append(f"  Non-goals: {'; '.join(map(str, non_goals))}")
        constraints = declared_intent.get("constraints")
        if isinstance(constraints, list) and constraints:
            lines.append(f"  Constraints: {'; '.join(map(str, constraints))}")
    if intent.get("roadmap_item") is not None:
        lines.append(
            f"  roadmap item: {intent['roadmap_item']} ({intent.get('roadmap_binding_status', 'recorded-not-bound')})"
        )
    if intent.get("roadmap_step") is not None:
        lines.append(
            f"  roadmap step: {intent['roadmap_step']} ({intent.get('roadmap_binding_status', 'recorded-not-bound')})"
        )

    lines.append(_execution_line(execution))
    for lane in execution.get("lanes", []):
        if not isinstance(lane, dict):
            continue
        lines.append(f"  lane {lane.get('lane_id', 'unknown')}")
        requested = lane.get("requested", {})
        observed = lane.get("observed", {})
        lines.append(
            f"    adapter | requested: {_display_value(requested.get('adapter'))} | observed: {_display_value(observed.get('adapter') or observed.get('adapter_status'))}"
        )
        if requested.get("model") is not None or observed.get("model") is not None or observed.get("model_status"):
            lines.append(
                f"    model   | requested: {_display_value(requested.get('model'))} | observed: {_display_value(observed.get('model') or observed.get('model_status') or observed.get('status'))}"
            )
    timeout_guidance = execution.get("timeout_guidance")
    if isinstance(timeout_guidance, list):
        lines.extend(f"  Timeout guidance: {item}" for item in timeout_guidance if isinstance(item, str))

    lines.append(_verification_line(verification))
    lines.append(f"  State: {summary.get('state', 'blocked')}")
    lines.append("Producer declarations (not verified):")
    declarations = payload.get("declarations")
    if isinstance(declarations, list) and declarations:
        lines.append(
            "  Declarations: producer-reported; not re-derived by Depone "
            "(signed bytes only)"
        )
        seen_declarations = set()
        for item in declarations:
            if not isinstance(item, dict):
                continue
            artifact = item.get("artifact", "unknown")
            if artifact in seen_declarations:
                continue
            seen_declarations.add(artifact)
            lines.append(f"  - {artifact}")
    else:
        lines.append("  none recorded")
    lines.append("Evidence:")
    lines.append(f"  run directory: {_display_value(evidence.get('run_dir') or payload.get('run_dir'))}")
    for artifact in evidence.get("artifacts", []):
        if isinstance(artifact, dict):
            lines.append(f"  {artifact.get('name', 'artifact')}: {artifact.get('sha256', 'hash unavailable')}")
    lines.append(f"  Handoff: {'packaged' if handoff.get('handoff_present') else 'not packaged'}")
    lines.append(f"  Next: {summary.get('recommended_next_action') or 'none'}")
    lines.append("  Human review:")
    focus = payload.get("human_review", {}).get("focus") if isinstance(payload.get("human_review"), dict) else None
    if isinstance(focus, list) and focus:
        lines.extend(f"  - {item}" for item in focus)
    else:
        lines.append("  - no specific reviewer focus recorded")
    return "\n".join(lines) + "\n"


def _verified_declared_intent(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, object]] | None:
    manifest_path = run_dir / "companion-manifest.json"
    intent_path = run_dir / "declared-intent.json"
    if not manifest_path.is_file() or not intent_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    reference = manifest.get("declared_intent_ref")
    if (
        not isinstance(reference, dict)
        or reference.get("declared") is not True
        or not isinstance(reference.get("path"), str)
        or not isinstance(reference.get("sha256"), str)
    ):
        return None
    try:
        if Path(reference["path"]).resolve(strict=False) != intent_path:
            return None
        if _output_hash_file(intent_path) != reference["sha256"]:
            return None
        from witnessd.orro_intent import read_declared_intent

        return read_declared_intent(intent_path), dict(reference)
    except Exception:  # noqa: BLE001 - unverified pickup must not block report
        return None


def _execution_line(execution: dict[str, Any]) -> str:
    if not execution.get("proofrun_evidence_present"):
        return "Execution: evidence missing"
    lane_count = execution.get("execution_lane_count", execution.get("lane_count", 0))
    reviewer_count = execution.get("reviewer_lane_count", 0)
    if execution.get("proofrun_evidence_present"):
        label = ""
        if lane_count == 1:
            label = " (single-lane policy selection)" if execution.get("policy_selected") else " (single-lane execution)"
        identities = []
        for axis in ("adapter", "model"):
            values = execution.get(f"{axis}_values")
            source = execution.get(f"{axis}_value_source")
            if isinstance(values, list) and values and source:
                identities.append(f"{axis}={','.join(map(str, values))} ({source})")
        suffix = "; requested: see columns; observed: see columns"
        if identities:
            suffix += "; " + "; ".join(identities)
        return f"Execution: {lane_count} execution lane{'s' if lane_count != 1 else ''}, {reviewer_count} reviewer lane{'s' if reviewer_count != 1 else ''}{label}{suffix}"
    return "Execution: evidence missing"


def _verification_line(verification: dict[str, Any]) -> str:
    if verification.get("proofcheck_verdict_present"):
        decision = verification.get("decision") or "unknown"
        return f"Verification: Depone proofcheck {decision}"
    return "Verification: proofcheck missing"


def _execution_value_source(execution: dict[str, Any]) -> str:
    sources = {
        execution.get("adapter_value_source"), execution.get("model_value_source")
    } - {None, "unknown"}
    if sources == {"observed"}:
        return "observed"
    if sources == {"requested"}:
        return "requested"
    if sources:
        return "mixed"
    return "unknown"


def _execution_counts_text(
    adapter_count: int, model_count: int, source: str, *, plural: bool = False
) -> str:
    counts = (
        f"{adapter_count} {'adapters' if plural else 'adapter'}, "
        f"{model_count} {'models' if plural else 'model'}"
    )
    return counts if source in {"observed", "unknown"} else f"{source}: {counts}"


def _summary(
    continuation: dict[str, Any],
    execution: dict[str, Any],
    verification: dict[str, Any],
    handoff: dict[str, Any],
    reference_adapter: dict[str, Any],
) -> dict[str, Any]:
    state = str(continuation.get("decision", "blocked"))
    if state not in {"invalid-run-dir", "blocked"} and not execution.get("proofrun_evidence_present"):
        state = "blocked"
    next_allowed = continuation.get("next_allowed")
    next_action = next_allowed[0] if isinstance(next_allowed, list) and next_allowed else None
    return {
        "state": state,
        "headline": _headline(
            state, execution, verification, handoff, reference_adapter
        ),
        "recommended_next_action": next_action,
        "ship_ready": bool(continuation.get("ship_ready")),
        "ship_command": continuation.get("ship_command"),
        "complete": state == "complete",
        "blocked": bool(continuation.get("blocked", False)),
        "not_real_ai_work": reference_adapter["not_real_ai_work"],
        "placeholder_fallback": reference_adapter["placeholder_fallback"],
    }


def _headline(
    state: str,
    execution: dict[str, Any],
    verification: dict[str, Any],
    handoff: dict[str, Any],
    reference_adapter: dict[str, Any],
) -> str:
    blocked_lanes = execution.get("blocked_lanes")
    if isinstance(blocked_lanes, list) and blocked_lanes:
        lane = blocked_lanes[0]
        if isinstance(lane, dict):
            return (
                f"Lane {lane.get('lane_id', 'unknown')} blocked — "
                f"{lane.get('blocked_reason') or 'no runtime reason reported'} "
                "(runtime-reported diagnostic)."
            )
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
        merged = dict(planned)
        merged.update(lane)
        summary_lanes.append(merged)
    execution_summary = summarize_executable_lanes(summary_lanes)
    detail_lanes = [_lane_identity_columns(lane) for lane in summary_lanes]
    reviewer_lane_count = sum(1 for lane in executed_lanes if _is_reviewer_lane(lane))
    policy_selected = len(summary_lanes) == 1 and (
        summary_lanes[0].get("model_source") == "model-policy"
    )
    lane_block = team_ledger_block_diagnostics(run_dir) or {}
    return {
        "proofrun_evidence_present": bool(observed.get("team_ledger")),
        "team_ledger_present": bool(observed.get("team_ledger")),
        "team_ledger_verdict_present": bool(observed.get("team_ledger_verdict")),
        **execution_summary,
        "lanes": detail_lanes,
        "observed_lane_count": len(executed_lanes),
        "execution_lane_count": max(0, len(executed_lanes) - reviewer_lane_count),
        "reviewer_lane_count": reviewer_lane_count,
        "policy_selected": policy_selected,
        **lane_block,
        "runner_roles": [
            role
            for role in continuation.get("role_status", [])
            if isinstance(role, dict) and role.get("phase") == "proofrun"
        ],
        "timeout_guidance": [
            str(lane["guidance"])
            for lane in summary_lanes
            if lane.get("blocked_reason")
            == "ERR_TEAM_LANE_TIMEOUT_COMMITTED_EVIDENCE_PENDING"
            and isinstance(lane.get("guidance"), str)
        ],
    }


def _lane_identity_columns(lane: dict[str, Any]) -> dict[str, Any]:
    requested_adapter = lane.get("adapter") or lane.get("team_adapter_kind")
    requested_model = lane.get("model")
    observed_adapter = lane.get("runner_adapter_kind") or lane.get("team_adapter_kind")
    observed_model = lane.get("runner_model") or lane.get("observed_model")
    adapter_status = None if observed_adapter else "no observation recorded"
    if observed_model:
        model_status = None
    elif requested_model is None:
        model_status = None
    elif observed_adapter == "agy":
        model_status = "provider returned no identity signal"
    else:
        model_status = "no observation recorded"
    return {
        "lane_id": lane.get("lane_id"),
        "requested": {"adapter": requested_adapter, "model": requested_model},
        "observed": {
            "adapter": observed_adapter,
            "model": observed_model,
            "adapter_status": adapter_status,
            "model_status": model_status,
        },
    }


def _is_reviewer_lane(lane: dict[str, Any]) -> bool:
    return (
        lane.get("phase") == "review"
        or lane.get("lane_intent") == "review"
        or lane.get("role_id") == "reviewer"
        or str(lane.get("lane_id", "")).startswith("reviewer")
    )


def _identity_summary(run_dir: Path) -> dict[str, Any]:
    ledger = _load_json_object(run_dir / "team-ledger.json") or {}
    identities = []
    for lane in ledger.get("lanes", []) if isinstance(ledger.get("lanes"), list) else []:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id", "unknown"))
        lane_dir = run_dir / str(lane.get("evidence_dir") or lane_id)
        intent_path = _find_lane_artifact(lane_dir, "run-intent.json")
        intent_payload = _load_json_object(intent_path) if intent_path else None
        signed_intent = intent_payload.get("intent", intent_payload) if isinstance(intent_payload, dict) else {}
        baseline = signed_intent.get("baseline", {}) if isinstance(signed_intent, dict) else {}
        worktree_path = run_dir / str(lane.get("worktree_receipt")) if lane.get("worktree_receipt") else None
        worktree = _load_json_object(worktree_path) if worktree_path else None
        if worktree is None:
            receipt_path = _find_lane_artifact(lane_dir, "worktree-lane-receipt.json")
            worktree = _load_json_object(receipt_path) if receipt_path else None
        runner_path = _find_lane_artifact(lane_dir, "runner-receipt.json")
        runner = _load_json_object(runner_path) if runner_path else None
        baseline_hash = baseline.get("git_status_sha256")
        working_tree = baseline.get("git_status_state") or "no observation recorded"
        if baseline_hash:
            working_tree = f"{working_tree}; baseline status hash {baseline_hash}"
        if isinstance(worktree, dict) and isinstance(worktree.get("dirty"), bool):
            working_tree += f"; final {'dirty' if worktree['dirty'] else 'clean'}"
        identities.append({
            "lane_id": lane_id,
            "base_commit": baseline.get("git_head"),
            "working_tree": working_tree,
            "branch": worktree.get("branch") if isinstance(worktree, dict) else None,
            "worktree": worktree.get("worktree") if isinstance(worktree, dict) else None,
            "task_id": runner.get("task_id") if isinstance(runner, dict) else None,
        })
    return {"lanes": identities}


def _intent_summary(run_dir: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    binding = _load_json_object(run_dir / "roadmap-binding.json")
    result: dict[str, Any] = {
        "profile": workflow.get("profile"),
        "roadmap_binding_status": "recorded-not-bound",
    }
    if isinstance(binding, dict):
        result["roadmap_item"] = binding.get("item_id")
        if binding.get("step_id") is not None:
            result["roadmap_step"] = binding.get("step_id")
    return result


def _evidence_summary(run_dir: Path) -> dict[str, Any]:
    names = (
        ("workflow-plan-binding", "workflow-plan-binding.json"),
        ("workflow-role-dispatch", "workflow-role-dispatch.json"),
        ("team-ledger", "team-ledger.json"),
        ("team-ledger-verdict", "team-ledger-verdict.json"),
        ("proofcheck-verdict", "proofcheck-verdict.json"),
    )
    artifacts = []
    for name, relative in names:
        path = run_dir / relative
        if path.is_file():
            artifacts.append({"name": name, "path": relative, "sha256": _hash_file(path)})
    return {"run_dir": str(run_dir), "artifacts": artifacts}


def _find_lane_artifact(lane_dir: Path, name: str) -> Path | None:
    direct = lane_dir / name
    if direct.is_file():
        return direct
    if lane_dir.is_dir():
        return next(iter(sorted(lane_dir.rglob(name))), None)
    return None


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "not recorded"
    return str(value)


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
    if summary.get("ship_ready"):
        focus.append("ship the branch; merge stays human")
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


def _declaration_summary(run_dir: Path) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for name in DECLARATION_ARTIFACTS:
        for path in sorted(run_dir.rglob(name)):
            payload = _load_json_object(path)
            if payload is None:
                continue
            declarations.append(
                {
                    "artifact": name.removesuffix(".json"),
                    "path": str(path.relative_to(run_dir)),
                    "evidence_substrate": "producer-transcribed",
                    "means": "producer-reported declaration; not bundle-bound or verifier-re-derived",
                    "can_change_evidence_verdict": False,
                    **{
                        f"producer_{key}": payload[key]
                        for key in ("verification_status", "conformance")
                        if key in payload
                    },
                }
            )
    return declarations


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
