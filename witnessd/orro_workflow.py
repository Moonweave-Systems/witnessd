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


ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN = "ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN"
ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED = "ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED"
ERR_ORRO_WORKFLOW_PLAN_INVALID = "ERR_ORRO_WORKFLOW_PLAN_INVALID"
ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH = "ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH"
ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED = "ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED"

WORKFLOW_PLAN_KIND = "orro-workflow-plan"
WORKFLOW_PLAN_SCHEMA_VERSION = "0.1"
WORKFLOW_PLAN_BINDING_KIND = "orro-workflow-plan-binding"
WORKFLOW_PLAN_BINDING_SCHEMA_VERSION = "0.1"

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
    "review-only",
    "verification-only",
    "docs-change",
    "release-readiness",
)


class OrroWorkflowError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def compile_workflow_plan(*, goal: str, profile: str) -> dict[str, Any]:
    if profile not in PROFILE_NAMES:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN)
    spec = _profile_spec(profile)
    return {
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


def load_workflow_plan(path: Path, *, expected_goal: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan must be a JSON object")
    plan = payload.get("workflow_plan") if "workflow_plan" in payload else payload
    if not isinstance(plan, dict):
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow_plan must be a JSON object")
    validate_workflow_plan(plan)
    if plan.get("goal") != expected_goal:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH, "workflow plan goal does not match proofrun goal")
    return deepcopy(plan)


def validate_workflow_plan(plan: dict[str, Any]) -> None:
    if plan.get("kind") != WORKFLOW_PLAN_KIND:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan kind is invalid")
    if plan.get("schema_version") != WORKFLOW_PLAN_SCHEMA_VERSION:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan schema_version is invalid")
    if plan.get("profile") not in PROFILE_NAMES:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan profile is invalid")
    boundary = plan.get("boundary")
    if not isinstance(boundary, dict) or boundary.get("orro_is_third_engine") is not False:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan boundary is invalid")
    if boundary.get("depone_verifies") is not True or boundary.get("witnessd_executes") is not True:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan boundary is invalid")
    roles = plan.get("roles")
    if not isinstance(roles, list):
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan roles must be a list")
    if plan.get("raises_assurance") is not None:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan must not claim assurance")
    for role in roles:
        if not isinstance(role, dict) or role.get("raises_assurance") is not False:
            raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_INVALID, "workflow plan role must not claim assurance")


def write_workflow_plan_binding(
    *,
    plan: dict[str, Any],
    source_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    plan_path = run_dir / "workflow-plan.json"
    binding_path = run_dir / "workflow-plan-binding.json"
    plan_sha256 = _canonical_hash(plan)
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
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED, str(exc)) from exc
    ref = workflow_plan_binding_ref(run_dir)
    if ref is None:
        raise OrroWorkflowError(ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED, "workflow plan binding was not readable")
    return ref


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


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _binding_boundary() -> dict[str, bool]:
    return {
        "approves_merge": False,
        "raises_assurance": False,
        "executes_commands": False,
        "verifies_evidence": False,
    }


def _profile_spec(profile: str) -> dict[str, Any]:
    specs: dict[str, dict[str, Any]] = {
        "code-change": {
            "roles": [
                _role("scout", "collect repository context before planning", "ORRO/witnessd", "scout"),
                _role("planner", "compile an execution plan without running workers", "ORRO/witnessd", "flowplan"),
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
                _role("handoff", "package review references after proofcheck", "ORRO/witnessd", "handoff"),
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
                _role("scout", "collect review context without execution", "ORRO/witnessd", "scout"),
                _role("reviewer", "inspect existing changes and evidence references", "ORRO", "flowplan"),
                _role("handoff", "package review notes without approval", "ORRO/witnessd", "handoff"),
            ],
            "flow": ["scout", "flowplan", "handoff"],
            "engine_calls": [
                _call("scout", "orro scout", "witnessd"),
                _call("flowplan", "orro flowplan", "ORRO"),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": [
                "review-only plan does not claim execution happened",
                "review-only handoff is intent; formal ORRO handoff still requires proofcheck",
                "handoff prose does not approve merge or raise assurance",
            ],
        },
        "verification-only": {
            "roles": [
                _role("verifier", "verify existing persisted evidence bytes", "Depone", "proofcheck", may_verify=True),
                _role("handoff", "package verifier decision references", "ORRO/witnessd", "handoff"),
            ],
            "flow": ["proofcheck", "handoff"],
            "engine_calls": [
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": [
                "proofcheck writes proofcheck-verdict.json",
                "handoff requires passing bound proofcheck verdict",
            ],
        },
        "docs-change": {
            "roles": [
                _role("scout", "collect documentation context", "ORRO/witnessd", "scout"),
                _role("planner", "plan documentation edits without execution", "ORRO/witnessd", "flowplan"),
                _role(
                    "runner",
                    "apply documentation changes and emit evidence when execution is needed",
                    "witnessd",
                    "proofrun",
                    may_execute=True,
                ),
                _role("verifier", "verify emitted evidence bytes", "Depone", "proofcheck", may_verify=True),
                _role("handoff", "package documentation change review", "ORRO/witnessd", "handoff"),
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
                _role("setup", "prepare local readiness metadata", "ORRO/witnessd", "init"),
                _role("doctor", "check local engine and adapter readiness", "ORRO/witnessd", "doctor"),
                _role("lock", "write or check distribution metadata", "ORRO/witnessd", "engine-lock"),
                _role("verifier", "verify release evidence bytes when supplied", "Depone", "proofcheck", may_verify=True),
                _role("handoff", "package release review references", "ORRO/witnessd", "handoff"),
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
) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "purpose": purpose,
        "engine": engine,
        "phase": phase,
        "may_execute": may_execute,
        "may_verify": may_verify,
        "raises_assurance": False,
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
