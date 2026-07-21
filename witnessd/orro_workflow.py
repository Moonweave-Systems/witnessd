"""ORRO workflow rolepack compiler v0.

This module is deliberately pure. It turns a goal and built-in profile into a
plan artifact; it does not execute workers, call models, verify evidence, or
mutate worktrees.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from witnessd.model_policy import resolve_policy_route
from witnessd.role_capability import RoleCapabilityGrant, grant_for_role
from witnessd.write_scope_declaration import write_scope_allows_paths


ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN = "ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN"
ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED = "ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED"
ERR_ORRO_WORKFLOW_PLAN_INVALID = "ERR_ORRO_WORKFLOW_PLAN_INVALID"
ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH = "ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH"
ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN = "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN"
ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED = "ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED"
ERR_ORRO_ROLE_LANE_PLAN_WRITE_FAILED = "ERR_ORRO_ROLE_LANE_PLAN_WRITE_FAILED"
ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED = "ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED"
ERR_ORRO_ROLE_LANE_PLAN_LOAD_FAILED = "ERR_ORRO_ROLE_LANE_PLAN_LOAD_FAILED"
ERR_ORRO_ROLE_LANE_PLAN_INVALID = "ERR_ORRO_ROLE_LANE_PLAN_INVALID"
ERR_ORRO_ROLE_LANE_PLAN_HASH_MISMATCH = "ERR_ORRO_ROLE_LANE_PLAN_HASH_MISMATCH"
ERR_ORRO_ROLE_LANE_PLAN_EXECUTION_FORBIDDEN = (
    "ERR_ORRO_ROLE_LANE_PLAN_EXECUTION_FORBIDDEN"
)
ERR_ORRO_ROLE_LANE_PLAN_EMPTY = "ERR_ORRO_ROLE_LANE_PLAN_EMPTY"
ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT = "ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT"
ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED = "ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED"
ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED = "ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED"
ERR_ORRO_ROLE_LANE_INTENT_INVALID = "ERR_ORRO_ROLE_LANE_INTENT_INVALID"
ERR_ORRO_VERIFICATION_CHECK_REQUIRED = "ERR_ORRO_VERIFICATION_CHECK_REQUIRED"
ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED = "ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED"
ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED = "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED"
ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION = "ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION"

WORKFLOW_PLAN_KIND = "orro-workflow-plan"
WORKFLOW_PLAN_SCHEMA_VERSION = "0.1"
WORKFLOW_PLAN_BINDING_KIND = "orro-workflow-plan-binding"
WORKFLOW_PLAN_BINDING_SCHEMA_VERSION = "0.1"
ROLE_LANE_PLAN_KIND = "orro-role-lane-plan"
ROLE_LANE_PLAN_SCHEMA_VERSION = "0.1"
ROLE_LANE_PLAN_BINDING_KIND = "orro-role-lane-plan-binding"
ROLE_LANE_PLAN_BINDING_SCHEMA_VERSION = "0.1"
ROLE_DISPATCH_KIND = "orro-role-dispatch"
ROLE_DISPATCH_SCHEMA_VERSION = "0.1"
ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX = "Execute ORRO role "
ROLE_LANE_ADAPTERS = ("shell", "codex", "claude", "agy", "gemini", "opencode")
# Review-only vendors (agy/gemini) must never land in an execution/proofrun
# lane, policy-resolved or not -- they have no execution role in this design,
# only a read-only review one (enforced in _validate_role_lane).
REVIEW_ONLY_ADAPTERS = ("agy", "gemini")
CLAUDE_CRITIC_CONTRACT = "claude-critic-v2.1"
EXECUTION_LANE_ADAPTERS = tuple(
    adapter for adapter in ROLE_LANE_ADAPTERS if adapter not in REVIEW_ONLY_ADAPTERS
)
VALID_LANE_INTENTS = frozenset({"implementation", "verification-only"})
ROLE_LANE_TIMEOUT_SECONDS_BY_TIER = {
    "quick": 120,
    "agentic": 1800,
    "frontier": 3600,
}

FORBIDDEN_ASSURANCE_SOURCES = [
    "skill text",
    "session transcript",
    "model confidence",
    "MCP output alone",
    "engine-lock",
    "doctor readiness",
    "handoff prose",
]

BOUNDARY = {
    "depone_verifies": True,
    "witnessd_executes": True,
    "orro_exposes_workflow": True,
    "orro_is_third_engine": False,
}

PROFILE_NAMES = (
    "code-change",
    "critic-only",
    "review-only",
    "verification-only",
    "docs-change",
    "release-readiness",
)


class OrroWorkflowError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def compile_workflow_plan(
    *, goal: str, profile: str, lane_intent: str | None = None
) -> dict[str, Any]:
    if profile not in PROFILE_NAMES:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN)
    spec = _profile_spec(profile)
    plan = {
        "kind": WORKFLOW_PLAN_KIND,
        "schema_version": WORKFLOW_PLAN_SCHEMA_VERSION,
        "goal": goal,
        "profile": profile,
        "roles": deepcopy(spec["roles"]),
        "flow": list(spec["flow"]),
        "engine_calls": deepcopy(spec["engine_calls"]),
        "required_gates": list(spec["required_gates"]),
        "forbidden_assurance_sources": list(FORBIDDEN_ASSURANCE_SOURCES),
        "boundary": dict(BOUNDARY),
    }
    if lane_intent is not None:
        if lane_intent not in VALID_LANE_INTENTS:
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_INTENT_INVALID,
                "workflow plan lane_intent is invalid",
            )
        if lane_intent == "implementation" and profile == "verification-only":
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_INTENT_INVALID,
                "verification-only profile cannot declare implementation lane intent",
            )
        for role in plan["roles"]:
            if role.get("phase") == "proofrun" and role.get("may_execute") is True:
                role["lane_intent"] = lane_intent
    return plan


def load_workflow_plan(
    path: Path, *, expected_goal: str | None = None
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan must be a JSON object"
        )
    plan = payload.get("workflow_plan") if "workflow_plan" in payload else payload
    if not isinstance(plan, dict):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow_plan must be a JSON object"
        )
    validate_workflow_plan(plan)
    if expected_goal is not None and plan.get("goal") != expected_goal:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH,
            "workflow plan goal does not match proofrun goal",
        )
    return deepcopy(plan)


def validate_workflow_plan(plan: dict[str, Any]) -> None:
    if plan.get("kind") != WORKFLOW_PLAN_KIND:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan kind is invalid"
        )
    if plan.get("schema_version") != WORKFLOW_PLAN_SCHEMA_VERSION:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan schema_version is invalid"
        )
    if plan.get("profile") not in PROFILE_NAMES:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan profile is invalid"
        )
    flow = plan.get("flow")
    if not isinstance(flow, list) or not all(isinstance(phase, str) for phase in flow):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan flow must be a string list"
        )
    roles = plan.get("roles")
    if not isinstance(roles, list):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan roles must be a list"
        )
    engine_calls = plan.get("engine_calls")
    if not isinstance(engine_calls, list):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan engine_calls must be a list"
        )
    boundary = plan.get("boundary")
    if (
        not isinstance(boundary, dict)
        or boundary.get("orro_is_third_engine") is not False
    ):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan boundary is invalid"
        )
    if (
        boundary.get("depone_verifies") is not True
        or boundary.get("witnessd_executes") is not True
        or boundary.get("orro_exposes_workflow") is not True
    ):
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan boundary is invalid"
        )
    if plan.get("raises_assurance") is not None:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan must not claim assurance"
        )
    for role in roles:
        if not isinstance(role, dict) or role.get("raises_assurance") is not False:
            raise OrroWorkflowError(
                ERR_ORRO_WORKFLOW_PLAN_INVALID,
                "workflow plan role must not claim assurance",
            )
        lane_intent = role.get("lane_intent")
        if lane_intent is not None and (
            not isinstance(lane_intent, str)
            or lane_intent not in VALID_LANE_INTENTS
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_INTENT_INVALID,
                "workflow plan role lane_intent is invalid",
            )
    for call in engine_calls:
        if not isinstance(call, dict):
            raise OrroWorkflowError(
                ERR_ORRO_WORKFLOW_PLAN_INVALID,
                "workflow plan engine_call must be a JSON object",
            )
        if call.get("executes") is True and call.get("verifies") is True:
            raise OrroWorkflowError(
                ERR_ORRO_WORKFLOW_PLAN_INVALID,
                "workflow plan engine_call cannot execute and verify",
            )


def workflow_plan_hash(plan: dict[str, Any]) -> str:
    validate_workflow_plan(plan)
    return _canonical_hash(plan)


def compile_role_lane_plan(
    *,
    workflow_plan: dict[str, Any],
    lane_adapter: str = "shell",
    tier: str = "quick",
    lane_timeout_seconds: int | None = None,
    policy: dict[str, Any] | None = None,
    rolepack: dict[str, Any] | None = None,
    check_commands: list[str] | None = None,
) -> dict[str, Any]:
    validate_workflow_plan(workflow_plan)
    if lane_adapter not in ROLE_LANE_ADAPTERS:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED,
            f"unsupported ORRO role lane adapter: {lane_adapter}",
        )
    if lane_timeout_seconds is not None and (
        type(lane_timeout_seconds) is not int
        or lane_timeout_seconds < 1
        or lane_timeout_seconds > 3600
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "lane timeout override must be an integer from 1 to 3600",
        )
    profile = str(workflow_plan["profile"])
    if check_commands is not None and profile != "verification-only":
        raise OrroWorkflowError(
            ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED,
            "check commands are only supported by the verification-only profile",
        )
    execution_allowed = profile in {
        "code-change",
        "docs-change",
        "verification-only",
    }
    lanes: list[dict[str, Any]] = []
    if profile == "verification-only":
        if lane_adapter != "shell":
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED,
                "verification-only lanes are deterministic shell lanes only",
            )
        checks = _normalized_check_commands(check_commands)
        if not checks:
            raise OrroWorkflowError(
                ERR_ORRO_VERIFICATION_CHECK_REQUIRED,
                "verification-only role lanes require at least one check command",
            )
        for role in workflow_plan["roles"]:
            if (
                isinstance(role, dict)
                and role.get("phase") == "proofrun"
                and role.get("may_execute") is True
            ):
                lanes.append(
                    _verify_lane_from_role(role, workflow_plan, tier, checks)
                )
    elif execution_allowed:
        for role in workflow_plan["roles"]:
            if (
                isinstance(role, dict)
                and role.get("phase") == "proofrun"
                and role.get("may_execute") is True
            ):
                lanes.append(
                    _role_lane_from_role(
                        role,
                        workflow_plan,
                        lane_adapter,
                        tier,
                        lane_timeout_seconds,
                        policy,
                        rolepack,
                    )
                )
    elif profile == "review-only" and (
        policy is not None or rolepack is not None or lane_adapter in {"agy", "gemini"}
    ):
        for role in workflow_plan["roles"]:
            if isinstance(role, dict) and role.get("role_id") == "reviewer":
                lanes.append(
                    _review_lane_from_role(
                        role, workflow_plan, lane_adapter, tier, policy, rolepack
                    )
                )
    elif profile == "critic-only":
        for role in workflow_plan["roles"]:
            if isinstance(role, dict) and role.get("role_id") == "critic":
                lanes.append(
                    _critic_lane_from_role(role, workflow_plan, tier, rolepack)
                )
    plan = {
        "kind": ROLE_LANE_PLAN_KIND,
        "schema_version": ROLE_LANE_PLAN_SCHEMA_VERSION,
        "workflow_plan_hash": workflow_plan_hash(workflow_plan),
        "workflow_profile": profile,
        "goal": workflow_plan["goal"],
        "execution_allowed": execution_allowed,
        "lanes": lanes,
        **summarize_executable_lanes(lanes),
        "boundary": _role_lane_plan_boundary(),
    }
    lane_scope_advisory = _whole_goal_lane_scope_advisory(lanes)
    if lane_scope_advisory:
        plan["lane_scope_advisory"] = lane_scope_advisory
    required_axes = _required_role_capability_axes(profile, lanes)
    if required_axes:
        plan["required_role_capability_axes"] = required_axes
    return plan


def summarize_executable_lanes(lanes: list[dict[str, Any]]) -> dict[str, Any]:
    executable_lanes = [
        lane
        for lane in lanes
        if lane.get("may_execute") is not False
        and lane.get("phase", "proofrun") == "proofrun"
    ]
    adapters = {
        str(adapter)
        for lane in executable_lanes
        if (
            adapter := lane.get("adapter")
            or lane.get("runner_adapter_kind")
            or lane.get("team_adapter_kind")
            or ("shell" if "commands" in lane else None)
        )
    }
    models = {
        str(model)
        for lane in executable_lanes
        if (model := lane.get("model")) is not None and str(model)
    }
    lane_count = len(executable_lanes)
    distinct_adapter_count = len(adapters)
    distinct_model_count = len(models)
    return {
        "lane_count": lane_count,
        "distinct_adapter_count": distinct_adapter_count,
        "distinct_model_count": distinct_model_count,
        "multi_model_execution": lane_count > 1
        and (distinct_adapter_count > 1 or distinct_model_count > 1),
    }


def _whole_goal_lane_scope_advisory(lanes: list[dict[str, Any]]) -> list[str]:
    advisories = []
    for lane in lanes:
        prompt = lane.get("prompt")
        if (
            lane.get("phase") != "proofrun"
            or lane.get("may_execute") is not True
            or not isinstance(prompt, str)
            or not prompt.startswith(ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX)
        ):
            continue
        advisories.append(
            f"lane '{lane['role_id']}' covers the entire goal at the "
            f"{lane['tier']} tier ({lane['timeout_seconds']}s); narrow the goal "
            "or set --role-lane-tier to change the budget."
        )
    return advisories


def _required_role_capability_axes(
    profile: str, lanes: list[dict[str, Any]]
) -> list[str]:
    if profile not in {"code-change", "docs-change"}:
        return []
    write_scope_required = any(
        lane.get("phase") == "proofrun"
        and lane.get("may_execute") is True
        and isinstance(lane.get("granted_write_scope"), list)
        and bool(lane["granted_write_scope"])
        for lane in lanes
    )
    tool_calls_required = any(
        lane.get("phase") == "proofrun"
        and lane.get("may_execute") is True
        and lane.get("adapter") == "claude"
        and isinstance(lane.get("granted_tools"), dict)
        for lane in lanes
    )
    skill_routing_required = any(
        lane.get("phase") == "proofrun"
        and lane.get("may_execute") is True
        and isinstance(lane.get("granted_skill_routing"), dict)
        for lane in lanes
    )
    return [
        axis
        for axis, required in (
            ("write_scope", write_scope_required),
            ("tool_calls", tool_calls_required),
            ("skill_routing", skill_routing_required),
        )
        if required
    ]


def write_role_lane_plan(path: Path, role_lane_plan: dict[str, Any]) -> dict[str, Any]:
    validate_role_lane_plan(role_lane_plan)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(role_lane_plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OrroWorkflowError(ERR_ORRO_ROLE_LANE_PLAN_WRITE_FAILED, str(exc)) from exc
    return role_lane_plan_file_ref(path)


def role_lane_plan_file_ref(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroWorkflowError(ERR_ORRO_ROLE_LANE_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan must be a JSON object"
        )
    validate_role_lane_plan(payload)
    execution_summary = summarize_executable_lanes(payload["lanes"])
    return {
        "path": str(path.resolve(strict=False)),
        "sha256": _hash_file(path),
        "workflow_plan_hash": payload["workflow_plan_hash"],
        "profile": payload["workflow_profile"],
        "goal": payload["goal"],
        **execution_summary,
        "boundary": payload["boundary"],
    }


def load_role_lane_plan(
    path: Path,
    *,
    workflow_plan: dict[str, Any],
    require_explicit_prompts: bool = True,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroWorkflowError(ERR_ORRO_ROLE_LANE_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan must be a JSON object"
        )
    validate_role_lane_plan(payload)
    expected_hash = workflow_plan_hash(workflow_plan)
    if payload.get("workflow_plan_hash") != expected_hash:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_HASH_MISMATCH,
            "role-lane plan is not bound to the supplied workflow plan",
        )
    if payload.get("execution_allowed") is not True:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_EXECUTION_FORBIDDEN,
            "role-lane plan does not allow proofrun execution",
        )
    lanes = payload.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_EMPTY,
            "role-lane plan has no executable lanes",
        )
    if require_explicit_prompts:
        assert_role_lane_prompts_explicit(payload)
    return deepcopy(payload)


def validate_role_lane_plan(plan: dict[str, Any]) -> None:
    if plan.get("kind") != ROLE_LANE_PLAN_KIND:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan kind is invalid"
        )
    if plan.get("schema_version") != ROLE_LANE_PLAN_SCHEMA_VERSION:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan schema_version is invalid"
        )
    if plan.get("workflow_profile") not in PROFILE_NAMES:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan profile is invalid"
        )
    if not isinstance(plan.get("goal"), str) or not plan.get("goal"):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan goal is invalid"
        )
    if (
        not isinstance(plan.get("workflow_plan_hash"), str)
        or len(plan["workflow_plan_hash"]) != 64
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan workflow hash is invalid"
        )
    if plan.get("execution_allowed") not in {True, False}:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan execution flag is invalid"
        )
    lanes = plan.get("lanes")
    if not isinstance(lanes, list):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan lanes must be a list"
        )
    lane_scope_advisory = plan.get("lane_scope_advisory")
    if lane_scope_advisory is not None and (
        not isinstance(lane_scope_advisory, list)
        or not all(
            isinstance(item, str) and item for item in lane_scope_advisory
        )
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "role-lane plan lane_scope_advisory must be a list of strings",
        )
    summary_keys = {
        "lane_count",
        "distinct_adapter_count",
        "distinct_model_count",
        "multi_model_execution",
    }
    present_summary_keys = summary_keys.intersection(plan)
    expected_summary = summarize_executable_lanes(lanes)
    if present_summary_keys and (
        present_summary_keys != summary_keys
        or any(plan[key] != value for key, value in expected_summary.items())
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "role-lane plan execution summary is invalid",
        )
    boundary = plan.get("boundary")
    if (
        not isinstance(boundary, dict)
        or boundary.get("depone_verifies") is not True
        or boundary.get("witnessd_executes") is not True
        or boundary.get("orro_exposes_workflow") is not True
        or boundary.get("role_lane_plan_is_proof") is not False
        or boundary.get("raises_assurance") is not False
        or boundary.get("approves_merge") is not False
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan boundary is invalid"
        )
    for lane in lanes:
        _validate_role_lane(lane, workflow_profile=str(plan["workflow_profile"]))


def assert_role_lane_prompts_explicit(plan: dict[str, Any]) -> None:
    lanes = plan.get("lanes")
    if not isinstance(lanes, list):
        return
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        prompt = lane.get("prompt")
        if isinstance(prompt, str) and prompt.startswith(
            ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
                f"role-lane {lane.get('lane_id')!r} still has a placeholder prompt",
            )


def write_role_lane_plan_binding(
    *,
    role_lane_plan: dict[str, Any],
    source_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    plan_path = run_dir / "role-lane-plan.json"
    binding_path = run_dir / "role-lane-plan-binding.json"
    validate_role_lane_plan(role_lane_plan)
    binding = {
        "kind": ROLE_LANE_PLAN_BINDING_KIND,
        "schema_version": ROLE_LANE_PLAN_BINDING_SCHEMA_VERSION,
        "role_lane_plan_path": "role-lane-plan.json",
        "role_lane_plan_sha256": _canonical_hash(role_lane_plan),
        "workflow_plan_hash": role_lane_plan["workflow_plan_hash"],
        "source_path": str(source_path),
        "goal": role_lane_plan["goal"],
        "profile": role_lane_plan["workflow_profile"],
        "boundary": _role_lane_plan_boundary(),
    }
    try:
        plan_path.write_text(
            json.dumps(role_lane_plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        binding_path.write_text(
            json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise OrroWorkflowError(ERR_ORRO_ROLE_LANE_PLAN_WRITE_FAILED, str(exc)) from exc
    ref = role_lane_plan_binding_ref(run_dir)
    if ref is None:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_WRITE_FAILED,
            "role-lane plan binding was not readable",
        )
    return ref


def assert_workflow_phase_allowed(plan: dict[str, Any], phase: str) -> None:
    validate_workflow_plan(plan)
    flow = plan["flow"]
    if phase not in flow:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN,
            f"workflow plan does not allow phase: {phase}",
        )
    if phase == "proofrun":
        engine_calls = plan["engine_calls"]
        allowed = any(
            isinstance(call, dict)
            and call.get("phase") == "proofrun"
            and call.get("engine") == "witnessd"
            and call.get("executes") is True
            and call.get("verifies") is False
            for call in engine_calls
        )
        if not allowed:
            raise OrroWorkflowError(
                ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN,
                "workflow plan does not allow witnessd proofrun execution",
            )


def write_workflow_plan_binding(
    *,
    plan: dict[str, Any],
    source_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    plan_path = run_dir / "workflow-plan.json"
    binding_path = run_dir / "workflow-plan-binding.json"
    plan_sha256 = workflow_plan_hash(plan)
    binding = {
        "kind": WORKFLOW_PLAN_BINDING_KIND,
        "schema_version": WORKFLOW_PLAN_BINDING_SCHEMA_VERSION,
        "workflow_plan_path": "workflow-plan.json",
        "workflow_plan_sha256": plan_sha256,
        "source_path": str(source_path),
        "goal": plan["goal"],
        "profile": plan["profile"],
        "boundary": _binding_boundary(),
    }
    try:
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        binding_path.write_text(
            json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED, str(exc)) from exc
    ref = workflow_plan_binding_ref(run_dir)
    if ref is None:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED,
            "workflow plan binding was not readable",
        )
    return ref


def write_workflow_role_dispatch(
    *,
    plan: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    dispatch_path = run_dir / "workflow-role-dispatch.json"
    dispatch = build_workflow_role_dispatch(plan=plan, run_dir=run_dir)
    try:
        dispatch_path.write_text(
            json.dumps(dispatch, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED, str(exc)) from exc
    ref = workflow_role_dispatch_ref(run_dir)
    if ref is None:
        raise OrroWorkflowError(
            ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED,
            "workflow role dispatch was not readable",
        )
    return ref


def build_workflow_role_dispatch(
    *, plan: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    validate_workflow_plan(plan)
    lane_ids = _team_ledger_lane_ids(run_dir / "team-ledger.json")
    has_team_ledger = (run_dir / "team-ledger.json").is_file()
    role_lane_ref = role_lane_plan_binding_ref(run_dir)
    roles = []
    for role in plan["roles"]:
        role_phase = str(role.get("phase", ""))
        role_record = {
            "role_id": role.get("role_id"),
            "phase": role_phase,
            "engine": role.get("engine"),
            "may_execute": role.get("may_execute") is True,
            "may_verify": role.get("may_verify") is True,
            "raises_assurance": False,
            "status": _role_status(role_phase, has_team_ledger),
            "evidence_refs": ["team-ledger.json"]
            if role_phase == "proofrun" and has_team_ledger
            else [],
        }
        if role_phase == "proofrun" and role_lane_ref is not None:
            role_record["evidence_refs"].append("role-lane-plan.json")
        if role_phase == "proofrun" and lane_ids:
            role_record["lane_ids"] = lane_ids
        roles.append(role_record)
    dispatch = {
        "kind": ROLE_DISPATCH_KIND,
        "schema_version": ROLE_DISPATCH_SCHEMA_VERSION,
        "workflow_plan_hash": workflow_plan_hash(plan),
        "workflow_profile": plan["profile"],
        "goal": plan["goal"],
        "run_dir": str(run_dir),
        "roles": roles,
        "boundary": {
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
            "role_dispatch_is_proof": False,
            "raises_assurance": False,
            "approves_merge": False,
        },
    }
    if role_lane_ref is not None:
        dispatch["role_lane_plan_hash"] = role_lane_ref["sha256"]
        dispatch["role_lane_plan"] = role_lane_ref
    return dispatch


def workflow_role_dispatch_ref(run_dir: Path) -> dict[str, Any] | None:
    dispatch_path = run_dir / "workflow-role-dispatch.json"
    if not dispatch_path.is_file():
        return None
    try:
        dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(dispatch, dict):
        return None
    return {
        "path": str(dispatch_path),
        "sha256": _hash_file(dispatch_path),
        "profile": dispatch.get("workflow_profile"),
        "goal": dispatch.get("goal"),
        "boundary": dispatch.get("boundary", _role_dispatch_boundary()),
    }


def workflow_plan_binding_ref(run_dir: Path) -> dict[str, Any] | None:
    binding_path = run_dir / "workflow-plan-binding.json"
    if not binding_path.is_file():
        return None
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(binding, dict):
        return None
    return {
        "path": str(run_dir / "workflow-plan.json"),
        "binding_path": str(binding_path),
        "sha256": binding.get("workflow_plan_sha256"),
        "profile": binding.get("profile"),
        "goal": binding.get("goal"),
        "boundary": binding.get("boundary", _binding_boundary()),
    }


def role_lane_plan_binding_ref(run_dir: Path) -> dict[str, Any] | None:
    binding_path = run_dir / "role-lane-plan-binding.json"
    if not binding_path.is_file():
        return None
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(binding, dict):
        return None
    return {
        "path": str(run_dir / "role-lane-plan.json"),
        "binding_path": str(binding_path),
        "sha256": binding.get("role_lane_plan_sha256"),
        "workflow_plan_hash": binding.get("workflow_plan_hash"),
        "profile": binding.get("profile"),
        "goal": binding.get("goal"),
        "boundary": binding.get("boundary", _role_lane_plan_boundary()),
    }


def _role_status(phase: str, has_team_ledger: bool) -> str:
    if phase == "proofrun":
        return "executed" if has_team_ledger else "pending-proofrun"
    if phase == "proofcheck":
        return "pending-proofcheck"
    if phase == "handoff":
        return "pending-handoff"
    return "planned"


def _team_ledger_lane_ids(ledger_path: Path) -> list[str]:
    if not ledger_path.is_file():
        return []
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lanes = ledger.get("lanes") if isinstance(ledger, dict) else None
    if not isinstance(lanes, list):
        return []
    lane_ids = []
    for lane in lanes:
        if isinstance(lane, dict) and isinstance(lane.get("lane_id"), str):
            lane_ids.append(lane["lane_id"])
    return sorted(lane_ids)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_lane_adapter_and_model(
    *,
    role_kind: str,
    tier: str,
    lane_adapter: str,
    policy: dict[str, Any] | None,
    grant: RoleCapabilityGrant | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (adapter, resolved_tier, extra_lane_fields) for a role lane.

    Without a policy this is the pre-existing uniform behavior: the caller's
    lane_adapter, no model field at all. With a policy, (role_kind, tier) must
    resolve to a concrete (adapter, model) -- an unmapped combo fails closed
    rather than silently falling back to lane_adapter, matching the "no quiet
    degradation" rule the rest of this feature follows.
    """
    if grant is not None and grant.model is not None:
        if len(grant.adapters) != 1:
            raise OrroWorkflowError(
                ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED,
                (
                    f"role_id={role_kind!r} pins model {grant.model!r} but does "
                    "not declare exactly one adapter"
                ),
            )
        adapter = grant.adapters[0]
        return (
            adapter,
            _resolve_role_lane_tier(tier, adapter=adapter),
            {"model": grant.model, "model_source": "rolepack"},
        )
    if policy is None:
        return lane_adapter, _resolve_role_lane_tier(tier, adapter=lane_adapter), {}
    policy_tier = tier
    route = None
    if tier == "auto":
        for candidate_tier in ("agentic", "quick"):
            candidate = resolve_policy_route(
                policy, role_kind=role_kind, tier=candidate_tier
            )
            if candidate is not None and _resolve_role_lane_tier(
                tier, adapter=str(candidate["adapter"])
            ) == candidate_tier:
                policy_tier = candidate_tier
                route = candidate
                break
    else:
        route = resolve_policy_route(policy, role_kind=role_kind, tier=tier)
    if route is None:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED,
            f"no model policy route for role_kind={role_kind!r} tier={tier!r}",
        )
    return route["adapter"], policy_tier, {
        "model": route["model"],
        "model_source": "model-policy",
        "budget": dict(route["budget"]),
        "resolved_via_policy": True,
        "policy_role_kind": role_kind,
        "policy_tier": policy_tier,
    }


def _resolve_role_lane_tier(tier: str, *, adapter: str) -> str:
    if tier == "auto":
        return "quick" if adapter == "shell" else "agentic"
    if tier not in ROLE_LANE_TIMEOUT_SECONDS_BY_TIER:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            f"unsupported ORRO role lane tier: {tier}",
        )
    return tier


def _grant_for_lane(
    *,
    rolepack: dict[str, Any] | None,
    role_id: str,
    phase: str,
) -> RoleCapabilityGrant | None:
    if rolepack is None:
        return None
    grant = grant_for_role(rolepack, role_id)
    if grant is None:
        raise OrroWorkflowError(
            ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED,
            f"rolepack does not grant role_id={role_id!r}",
        )
    expected_capability = "execute" if phase == "proofrun" else "review"
    if grant.capability != expected_capability:
        raise OrroWorkflowError(
            ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED,
            (
                f"role_id={role_id!r} grant capability {grant.capability!r} "
                f"does not match phase {phase!r}"
            ),
        )
    return grant


def _role_capability_for_lane(
    *,
    grant: RoleCapabilityGrant | None,
    role_id: str,
    phase: str,
    adapter: str,
    region: list[str],
) -> dict[str, Any]:
    if grant is None:
        return {}
    if adapter not in grant.adapters:
        raise OrroWorkflowError(
            ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED,
            (
                f"adapter {adapter!r} is not granted for role_id={role_id!r} "
                f"capability={grant.capability!r}"
            ),
        )
    if (
        phase == "proofrun"
        and grant.write_scope is not None
        and not write_scope_allows_paths(region, list(grant.write_scope))
    ):
        raise OrroWorkflowError(
            ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION,
            f"role_id={role_id!r} lane region is outside declared write_scope",
        )
    return _role_capability_lane_fields(grant)


def _role_capability_lane_fields(grant: RoleCapabilityGrant) -> dict[str, Any]:
    return {
        "granted_adapters": list(grant.adapters),
        "role_capability": grant.to_dict(),
        **(
            {"granted_write_scope": list(grant.write_scope)}
            if grant.write_scope is not None
            else {}
        ),
        **(
            {
                "granted_tools": {
                    "mcp": list(grant.tools["mcp"]),
                    "allow": list(grant.tools["allow"]),
                }
            }
            if grant.tools is not None
            else {}
        ),
        **(
            {"granted_skill_routing": dict(grant.skill_routing)}
            if grant.skill_routing is not None
            else {}
        ),
    }


def _normalized_check_commands(check_commands: list[str] | None) -> list[str]:
    if check_commands is None:
        return []
    return [
        check
        for check in check_commands
        if isinstance(check, str) and check.strip()
    ]


def _verify_lane_from_role(
    role: dict[str, Any],
    workflow_plan: dict[str, Any],
    tier: str,
    check_commands: list[str],
) -> dict[str, Any]:
    role_id = str(role["role_id"])
    resolved_tier = _resolve_role_lane_tier(tier, adapter="shell")
    digest = hashlib.sha256(
        f"{workflow_plan['goal']}:verification-only:{role_id}:shell".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "lane_id": f"{role_id}-{digest}",
        "role_id": role_id,
        "role_purpose": role.get("purpose", ""),
        "phase": "proofrun",
        "engine": "witnessd",
        "adapter": "shell",
        "tier": resolved_tier,
        "timeout_seconds": ROLE_LANE_TIMEOUT_SECONDS_BY_TIER[resolved_tier],
        "region": [],
        "prompt": (
            "Run declared verification checks under observation: "
            + "; ".join(check_commands)
        ),
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "may_execute": True,
        "may_verify": False,
        "raises_assurance": False,
        "lane_intent": "verification-only",
        "check_commands": list(check_commands),
    }


def _role_lane_from_role(
    role: dict[str, Any],
    workflow_plan: dict[str, Any],
    lane_adapter: str,
    tier: str,
    lane_timeout_seconds: int | None,
    policy: dict[str, Any] | None,
    rolepack: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = str(workflow_plan["profile"])
    role_id = str(role["role_id"])
    grant = _grant_for_lane(rolepack=rolepack, role_id=role_id, phase="proofrun")
    resolved_adapter, resolved_tier, extra = _resolve_lane_adapter_and_model(
        role_kind=role_id,
        tier=tier,
        lane_adapter=lane_adapter,
        policy=policy,
        grant=grant,
    )
    digest = hashlib.sha256(
        f"{workflow_plan['goal']}:{profile}:{role_id}:{resolved_adapter}".encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    lane_id = f"{role_id}-{digest}"
    region = _execution_region_from_grant(grant)
    if profile == "code-change" and not region:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED,
            "code-change proofrun lane requires a concrete rolepack write_scope",
        )
    if profile == "docs-change" and not region:
        region = [f"docs/{lane_id}.txt"]
    if profile == "docs-change" and grant is None:
        grant = RoleCapabilityGrant(
            role_id=role_id,
            capability="execute",
            adapters=(resolved_adapter,),
            write_scope=tuple(region),
        )
    role_capability = _role_capability_for_lane(
        grant=grant,
        role_id=role_id,
        phase="proofrun",
        adapter=resolved_adapter,
        region=region,
    )
    return {
        "lane_id": lane_id,
        "role_id": role_id,
        "role_purpose": role.get("purpose", ""),
        "phase": "proofrun",
        "engine": "witnessd",
        "adapter": resolved_adapter,
        "tier": resolved_tier,
        "timeout_seconds": (
            lane_timeout_seconds
            if lane_timeout_seconds is not None
            else ROLE_LANE_TIMEOUT_SECONDS_BY_TIER[resolved_tier]
        ),
        "region": region,
        "prompt": (
            f"{ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX}{role_id} "
            f"for goal: {workflow_plan['goal']}"
        ),
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "may_execute": True,
        "may_verify": False,
        "raises_assurance": False,
        **(
            {"lane_intent": role["lane_intent"]}
            if role.get("lane_intent") is not None
            else {}
        ),
        **role_capability,
        **extra,
    }


def _execution_region_from_grant(grant: RoleCapabilityGrant | None) -> list[str]:
    if grant is None or grant.write_scope is None:
        return []
    return [item for item in grant.write_scope if item]


def _review_lane_from_role(
    role: dict[str, Any],
    workflow_plan: dict[str, Any],
    lane_adapter: str,
    tier: str,
    policy: dict[str, Any] | None,
    rolepack: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = str(workflow_plan["profile"])
    role_id = str(role["role_id"])
    grant = _grant_for_lane(rolepack=rolepack, role_id=role_id, phase="review")
    resolved_adapter, resolved_tier, extra = _resolve_lane_adapter_and_model(
        role_kind=role_id,
        tier=tier,
        lane_adapter=lane_adapter,
        policy=policy,
        grant=grant,
    )
    role_capability = _role_capability_for_lane(
        grant=grant,
        role_id=role_id,
        phase="review",
        adapter=resolved_adapter,
        region=["."],
    )
    digest = hashlib.sha256(
        f"{workflow_plan['goal']}:{profile}:{role_id}:{resolved_adapter}".encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    lane_id = f"{role_id}-{digest}"
    return {
        "lane_id": lane_id,
        "role_id": role_id,
        "role_purpose": role.get("purpose", ""),
        "phase": "review",
        "engine": "witnessd",
        "adapter": resolved_adapter,
        "tier": resolved_tier,
        "region": ["."],
        "prompt": f"Review ORRO goal without editing files: {workflow_plan['goal']}",
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "may_execute": False,
        "may_verify": False,
        "raises_assurance": False,
        **(
            {"lane_intent": role["lane_intent"]}
            if role.get("lane_intent") is not None
            else {}
        ),
        **role_capability,
        **extra,
    }


def _critic_lane_from_role(
    role: dict[str, Any],
    workflow_plan: dict[str, Any],
    tier: str,
    rolepack: dict[str, Any] | None,
) -> dict[str, Any]:
    role_id = str(role["role_id"])
    grant = _grant_for_lane(rolepack=rolepack, role_id=role_id, phase="review")
    resolved_adapter, resolved_tier, extra = _resolve_lane_adapter_and_model(
        role_kind=role_id,
        tier=tier,
        lane_adapter="claude",
        policy=None,
        grant=grant,
    )
    if resolved_adapter != "claude":
        raise OrroWorkflowError(
            ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED,
            "critic-only role must use the dedicated Claude critic adapter",
        )
    role_capability = _role_capability_for_lane(
        grant=grant,
        role_id=role_id,
        phase="review",
        adapter=resolved_adapter,
        region=["."],
    )
    digest = hashlib.sha256(
        f"{workflow_plan['goal']}:critic-only:{role_id}:claude".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "lane_id": f"{role_id}-{digest}",
        "role_id": role_id,
        "role_purpose": role.get("purpose", ""),
        "phase": "review",
        "engine": "witnessd",
        "adapter": "claude",
        "critic_contract": CLAUDE_CRITIC_CONTRACT,
        "tier": resolved_tier,
        "region": ["."],
        "prompt": (
            "Critique the ORRO goal without editing files or changing evidence "
            f"verdicts: {workflow_plan['goal']}"
        ),
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "may_execute": False,
        "may_verify": False,
        "raises_assurance": False,
        **role_capability,
        **extra,
    }


def _validate_role_lane(lane: Any, *, workflow_profile: str) -> None:
    if not isinstance(lane, dict):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane plan lane must be a JSON object"
        )
    for field in ("lane_id", "role_id", "phase", "engine", "adapter", "tier", "prompt"):
        if not isinstance(lane.get(field), str) or not lane.get(field):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID, f"role-lane field is invalid: {field}"
            )
    if lane["adapter"] not in ROLE_LANE_ADAPTERS:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED, "role-lane adapter is unsupported"
        )
    if lane["tier"] not in ROLE_LANE_TIMEOUT_SECONDS_BY_TIER:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "role-lane tier must be resolved before emission",
        )
    if (
        lane.get("phase") not in {"proofrun", "review"}
        or lane.get("engine") != "witnessd"
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane phase or engine is invalid"
        )
    if lane.get("phase") == "proofrun":
        if (
            lane.get("may_execute") is not True
            or lane.get("may_verify") is not False
            or lane.get("adapter") not in EXECUTION_LANE_ADAPTERS
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane execution boundary is invalid",
            )
    elif workflow_profile == "critic-only":
        if (
            lane.get("adapter") != "claude"
            or lane.get("role_id") != "critic"
            or lane.get("critic_contract") != CLAUDE_CRITIC_CONTRACT
            or lane.get("may_execute") is not False
            or lane.get("may_verify") is not False
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "dedicated Claude critic lane boundary is invalid",
            )
    else:
        if (
            lane.get("adapter") not in REVIEW_ONLY_ADAPTERS
            or lane.get("may_execute") is not False
            or lane.get("may_verify") is not False
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane review boundary is invalid"
            )
    if lane.get("raises_assurance") is not False:
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane must not claim assurance"
        )
    lane_intent = lane.get("lane_intent")
    if lane_intent is not None and (
        not isinstance(lane_intent, str) or lane_intent not in VALID_LANE_INTENTS
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_INTENT_INVALID,
            "role-lane lane_intent is invalid",
        )
    if lane_intent == "verification-only" and not list(lane.get("region") or []):
        checks = lane.get("check_commands")
        if (
            not isinstance(checks, list)
            or not checks
            or not all(
                isinstance(check, str) and check.strip() for check in checks
            )
        ):
            raise OrroWorkflowError(
                ERR_ORRO_VERIFICATION_CHECK_REQUIRED,
                "claimless verification-only lane requires non-empty check_commands",
            )
    if "model" in lane and (not isinstance(lane["model"], str) or not lane["model"]):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "role-lane model must be a non-empty string",
        )
    if "timeout_seconds" in lane and (
        type(lane["timeout_seconds"]) is not int
        or lane["timeout_seconds"] < 1
        or lane["timeout_seconds"] > 3600
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID,
            "role-lane timeout_seconds must be an integer from 1 to 3600",
        )
    if "granted_adapters" in lane:
        granted_adapters = lane["granted_adapters"]
        if (
            not isinstance(granted_adapters, list)
            or not granted_adapters
            or not all(isinstance(adapter, str) and adapter for adapter in granted_adapters)
            or lane["adapter"] not in granted_adapters
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane granted_adapters is invalid",
            )
    if "role_capability" in lane:
        role_capability = lane["role_capability"]
        if not isinstance(role_capability, dict):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane role_capability is invalid",
            )
        if role_capability.get("role_id") != lane["role_id"]:
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane role_capability role_id is invalid",
            )
        expected_capability = "execute" if lane["phase"] == "proofrun" else "review"
        if role_capability.get("capability") != expected_capability:
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane role_capability capability is invalid",
            )
    if "granted_write_scope" in lane:
        granted_write_scope = lane["granted_write_scope"]
        if not isinstance(granted_write_scope, list) or not all(
            isinstance(item, str) and item for item in granted_write_scope
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane granted_write_scope is invalid",
            )
    if "granted_tools" in lane:
        granted_tools = lane["granted_tools"]
        if (
            not isinstance(granted_tools, dict)
            or set(granted_tools) != {"mcp", "allow"}
            or not all(
                isinstance(granted_tools[key], list)
                and all(isinstance(item, str) and item for item in granted_tools[key])
                for key in ("mcp", "allow")
            )
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane granted_tools is invalid",
            )
    if "granted_skill_routing" in lane:
        try:
            from witnessd.skill_routing_declaration import (
                normalize_skill_routing_declaration,
            )

            normalize_skill_routing_declaration(lane["granted_skill_routing"])
        except ValueError as exc:
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLAN_INVALID,
                "role-lane granted_skill_routing is invalid",
            ) from exc
    region = lane.get("region")
    if (
        not isinstance(region, list)
        or (lane_intent != "verification-only" and not region)
        or not all(isinstance(item, str) and item for item in region)
    ):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane region is invalid"
        )
    budget = lane.get("budget")
    if not isinstance(budget, dict):
        raise OrroWorkflowError(
            ERR_ORRO_ROLE_LANE_PLAN_INVALID, "role-lane budget is invalid"
        )


def _binding_boundary() -> dict[str, bool]:
    return {
        "approves_merge": False,
        "raises_assurance": False,
        "executes_commands": False,
        "verifies_evidence": False,
    }


def _role_dispatch_boundary() -> dict[str, bool]:
    return {
        "depone_verifies": True,
        "witnessd_executes": True,
        "orro_exposes_workflow": True,
        "role_dispatch_is_proof": False,
        "raises_assurance": False,
        "approves_merge": False,
    }


def _role_lane_plan_boundary() -> dict[str, bool]:
    return {
        "depone_verifies": True,
        "witnessd_executes": True,
        "orro_exposes_workflow": True,
        "role_lane_plan_is_proof": False,
        "raises_assurance": False,
        "approves_merge": False,
    }


def _profile_spec(profile: str) -> dict[str, Any]:
    specs: dict[str, dict[str, Any]] = {
        "code-change": {
            "roles": [
                _role(
                    "scout",
                    "collect repository context before planning",
                    "ORRO/witnessd",
                    "scout",
                ),
                _role(
                    "planner",
                    "compile an execution plan without running workers",
                    "ORRO/witnessd",
                    "flowplan",
                ),
                _role(
                    "runner",
                    "execute the planned work and emit evidence",
                    "witnessd",
                    "proofrun",
                    may_execute=True,
                ),
                _role(
                    "verifier",
                    "verify persisted evidence bytes",
                    "Depone",
                    "proofcheck",
                    may_verify=True,
                ),
                _role(
                    "handoff",
                    "package review references after proofcheck",
                    "ORRO/witnessd",
                    "handoff",
                ),
            ],
            "flow": ["scout", "flowplan", "proofrun", "proofcheck", "handoff"],
            "engine_calls": [
                _call("scout", "orro scout", "witnessd"),
                _call("flowplan", "orro flowplan", "ORRO"),
                _call("proofrun", "orro proofrun", "witnessd", executes=True),
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": _evidence_handoff_gates(),
        },
        "review-only": {
            "roles": [
                _role(
                    "scout",
                    "collect review context without execution",
                    "ORRO/witnessd",
                    "scout",
                ),
                _role(
                    "reviewer",
                    "inspect existing changes and evidence references",
                    "ORRO",
                    "flowplan",
                ),
            ],
            "flow": ["scout", "flowplan"],
            "engine_calls": [
                _call("scout", "orro scout", "witnessd"),
                _call("flowplan", "orro flowplan", "ORRO"),
            ],
            "required_gates": [
                "review-only plan does not claim execution happened",
                "review-only handoff is intent; formal ORRO handoff still requires proofcheck",
                "handoff prose does not approve merge or raise assurance",
            ],
        },
        "critic-only": {
            "roles": [
                _role(
                    "scout",
                    "collect critic context without execution",
                    "ORRO/witnessd",
                    "scout",
                ),
                _role(
                    "critic",
                    "critique existing changes under a dedicated read-only contract",
                    "witnessd",
                    "flowplan",
                ),
            ],
            "flow": ["scout", "flowplan"],
            "engine_calls": [
                _call("scout", "orro scout", "witnessd"),
                _call("flowplan", "orro flowplan", "ORRO"),
            ],
            "required_gates": [
                "critic-only plan compiles exactly one dedicated Claude lane",
                "critic output is advisory and cannot change evidence verdicts",
                "critic sandbox mutation blocks the lane",
            ],
        },
        "verification-only": {
            "roles": [
                _role(
                    "check-runner",
                    "run declared verification checks under observation "
                    "without a write region",
                    "witnessd",
                    "proofrun",
                    may_execute=True,
                    lane_intent="verification-only",
                ),
                _role(
                    "verifier",
                    "verify existing persisted evidence bytes",
                    "Depone",
                    "proofcheck",
                    may_verify=True,
                ),
                _role(
                    "handoff",
                    "package verifier decision references",
                    "ORRO/witnessd",
                    "handoff",
                ),
            ],
            "flow": ["proofrun", "proofcheck", "handoff"],
            "engine_calls": [
                _call("proofrun", "orro proofrun", "witnessd", executes=True),
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": [
                "verification-only lane runs declared checks with an empty write region",
                "verification-only lane mutation is falsified by Depone",
                "proofcheck writes proofcheck-verdict.json",
                "handoff requires passing bound proofcheck verdict",
            ],
        },
        "docs-change": {
            "roles": [
                _role(
                    "scout", "collect documentation context", "ORRO/witnessd", "scout"
                ),
                _role(
                    "planner",
                    "plan documentation edits without execution",
                    "ORRO/witnessd",
                    "flowplan",
                ),
                _role(
                    "runner",
                    "apply documentation changes and emit evidence when execution is needed",
                    "witnessd",
                    "proofrun",
                    may_execute=True,
                ),
                _role(
                    "verifier",
                    "verify emitted evidence bytes",
                    "Depone",
                    "proofcheck",
                    may_verify=True,
                ),
                _role(
                    "handoff",
                    "package documentation change review",
                    "ORRO/witnessd",
                    "handoff",
                ),
            ],
            "flow": ["scout", "flowplan", "proofrun", "proofcheck", "handoff"],
            "engine_calls": [
                _call("scout", "orro scout", "witnessd"),
                _call("flowplan", "orro flowplan", "ORRO"),
                _call("proofrun", "orro proofrun", "witnessd", executes=True),
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": _evidence_handoff_gates(),
        },
        "release-readiness": {
            "roles": [
                _role(
                    "setup", "prepare local readiness metadata", "ORRO/witnessd", "init"
                ),
                _role(
                    "doctor",
                    "check local engine and adapter readiness",
                    "ORRO/witnessd",
                    "doctor",
                ),
                _role(
                    "lock",
                    "write or check distribution metadata",
                    "ORRO/witnessd",
                    "engine-lock",
                ),
                _role(
                    "verifier",
                    "verify release evidence bytes when supplied",
                    "Depone",
                    "proofcheck",
                    may_verify=True,
                ),
                _role(
                    "handoff",
                    "package release review references",
                    "ORRO/witnessd",
                    "handoff",
                ),
            ],
            "flow": ["init", "doctor", "engine-lock", "proofcheck", "handoff"],
            "engine_calls": [
                _call("init", "orro init", "witnessd"),
                _call("doctor", "orro doctor", "ORRO"),
                _call("engine-lock", "orro engine-lock", "ORRO"),
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": [
                "doctor readiness is not assurance",
                "engine-lock is distribution metadata only",
                "proofcheck writes proofcheck-verdict.json before handoff when evidence is supplied",
                "handoff requires passing bound proofcheck verdict",
            ],
        },
    }
    return specs[profile]


def _role(
    role_id: str,
    purpose: str,
    engine: str,
    phase: str,
    *,
    may_execute: bool = False,
    may_verify: bool = False,
    lane_intent: str | None = None,
) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "purpose": purpose,
        "engine": engine,
        "phase": phase,
        "may_execute": may_execute,
        "may_verify": may_verify,
        "raises_assurance": False,
        **({"lane_intent": lane_intent} if lane_intent is not None else {}),
    }


def _call(
    phase: str,
    command: str,
    engine: str,
    *,
    executes: bool = False,
    verifies: bool = False,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "command": command,
        "engine": engine,
        "executes": executes,
        "verifies": verifies,
    }


def _evidence_handoff_gates() -> list[str]:
    return [
        "proofrun emits evidence",
        "proofcheck writes proofcheck-verdict.json",
        "handoff requires passing bound proofcheck verdict",
    ]
