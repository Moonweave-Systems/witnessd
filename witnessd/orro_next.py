"""ORRO non-executing continuation gate v0.

This module reads persisted ORRO run artifacts and recommends the next safe
action. It does not execute workers, call Depone, repair evidence, or raise
assurance.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any


CONTINUATION_KIND = "orro-continuation-decision"
CONTINUATION_SCHEMA_VERSION = "0.1"

ERR_ORRO_NEXT_RUN_DIR_INVALID = "ERR_ORRO_NEXT_RUN_DIR_INVALID"
ERR_ORRO_NEXT_ARTIFACT_LOAD_FAILED = "ERR_ORRO_NEXT_ARTIFACT_LOAD_FAILED"
ERR_ORRO_NEXT_PROOFCHECK_NOT_PASS = "ERR_ORRO_NEXT_PROOFCHECK_NOT_PASS"
ERR_ORRO_NEXT_PROOFCHECK_UNBOUND = "ERR_ORRO_NEXT_PROOFCHECK_UNBOUND"
ERR_ORRO_NEXT_PROOFCHECK_BINDING_MISMATCH = "ERR_ORRO_NEXT_PROOFCHECK_BINDING_MISMATCH"
ERR_ORRO_NEXT_HANDOFF_LOAD_FAILED = "ERR_ORRO_NEXT_HANDOFF_LOAD_FAILED"
ERR_ORRO_NEXT_HANDOFF_UNBOUND = "ERR_ORRO_NEXT_HANDOFF_UNBOUND"
ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH = "ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH"
ERR_ORRO_NEXT_LANE_BLOCKED = "ERR_ORRO_NEXT_LANE_BLOCKED"

_CRITICAL_JSON_ARTIFACTS = (
    "workflow-plan.json",
    "workflow-plan-binding.json",
    "role-lane-plan.json",
    "role-lane-plan-binding.json",
    "workflow-role-dispatch.json",
    "team-ledger.json",
    "team-ledger-verdict.json",
)

_ARTIFACT_FILES = {
    "workflow_plan": "workflow-plan.json",
    "workflow_plan_binding": "workflow-plan-binding.json",
    "role_lane_plan": "role-lane-plan.json",
    "role_lane_plan_binding": "role-lane-plan-binding.json",
    "workflow_role_dispatch": "workflow-role-dispatch.json",
    "team_ledger": "team-ledger.json",
    "team_ledger_verdict": "team-ledger-verdict.json",
    "proofcheck_verdict": "proofcheck-verdict.json",
    "handoff": "orro-handoff.json",
}


class OrroNextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def decide_next(run_dir: Path, *, home: Path | None = None) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False) if home is not None else run_dir.parent.parent
    if not run_dir.is_dir():
        payload = _base_decision(
            run_dir,
            decision="invalid-run-dir",
            blocked=True,
            reasons=[f"run directory is missing: {run_dir}"],
            home=home,
        )
        payload["error"] = {
            "code": ERR_ORRO_NEXT_RUN_DIR_INVALID,
            "message": "run directory is missing",
        }
        return 2, payload

    observed = _observed_artifacts(run_dir)
    critical_error = _critical_artifact_error(run_dir)
    if critical_error is not None:
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=[critical_error],
            home=home,
        )
        payload["error"] = {
            "code": ERR_ORRO_NEXT_ARTIFACT_LOAD_FAILED,
            "message": critical_error,
        }
        return 1, payload

    proofcheck_payload, proofcheck_error = _load_optional_json(run_dir / "proofcheck-verdict.json")
    if proofcheck_error is not None:
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=[proofcheck_error],
            home=home,
        )
        payload["error"] = {
            "code": ERR_ORRO_NEXT_ARTIFACT_LOAD_FAILED,
            "message": proofcheck_error,
        }
        return 1, payload
    handoff_payload, handoff_error = _load_optional_json(run_dir / "orro-handoff.json")
    if handoff_error is not None:
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=[handoff_error],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload["error"] = {
            "code": ERR_ORRO_NEXT_HANDOFF_LOAD_FAILED,
            "message": handoff_error,
        }
        return 1, payload

    lane_block = team_ledger_block_diagnostics(run_dir)
    if lane_block is not None:
        blocked_lanes = lane_block["blocked_lanes"]
        reasons = [
            "lane "
            f"{lane.get('lane_id', 'unknown')} blocked — "
            f"{lane.get('blocked_reason') or 'no runtime reason reported'} "
            "(runtime-reported diagnostic from team-ledger-verdict.json)"
            for lane in blocked_lanes
        ]
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=reasons,
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload.update(lane_block)
        payload["next_allowed"] = [lane_block["diagnostic_command"]]
        payload["error"] = {
            "code": ERR_ORRO_NEXT_LANE_BLOCKED,
            "message": reasons[0],
        }
        return 1, payload

    proofcheck_state = _proofcheck_state(run_dir, proofcheck_payload)
    handoff_exists = observed["handoff"]
    has_run_evidence = observed["team_ledger"]

    if proofcheck_state["error"] is not None:
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=[str(proofcheck_state["reason"])],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload["error"] = proofcheck_state["error"]
        return 1, payload

    if proofcheck_state["decision"] == "pass" and handoff_exists:
        handoff_state = _handoff_state(run_dir, handoff_payload)
        if handoff_state["error"] is not None:
            payload = _base_decision(
                run_dir,
                decision="blocked",
                blocked=True,
                reasons=[str(handoff_state["reason"])],
                home=home,
                proofcheck_payload=proofcheck_payload,
            )
            payload["error"] = handoff_state["error"]
            return 1, payload
        payload = _base_decision(
            run_dir,
            decision="complete",
            blocked=False,
            reasons=[],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        return 0, payload
    if proofcheck_state["decision"] == "pass":
        payload = _base_decision(
            run_dir,
            decision="ready-for-handoff",
            blocked=False,
            reasons=[],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload["next_allowed"] = [
            f"orro handoff {run_dir} --home {home} "
            f"--out {run_dir / 'orro-handoff.json'}"
        ]
        return 0, payload
    if proofcheck_state["decision"] not in {None, "pass"}:
        payload = _base_decision(
            run_dir,
            decision="blocked",
            blocked=True,
            reasons=["proofcheck-verdict.json decision is not pass"],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload["error"] = {
            "code": ERR_ORRO_NEXT_PROOFCHECK_NOT_PASS,
            "message": "proofcheck-verdict.json decision is not pass",
        }
        return 1, payload
    if has_run_evidence:
        payload = _base_decision(
            run_dir,
            decision="needs-proofcheck",
            blocked=False,
            reasons=[],
            home=home,
            proofcheck_payload=proofcheck_payload,
        )
        payload["next_allowed"] = [
            f"orro proofcheck {run_dir} --home {home} --out {run_dir / 'proofcheck-verdict.json'}"
        ]
        return 0, payload

    decision = "evidence-pending" if _looks_like_partial_orro_dir(observed) else "blocked"
    payload = _base_decision(
        run_dir,
        decision=decision,
        blocked=True,
        reasons=["run evidence is missing"],
        home=home,
        proofcheck_payload=proofcheck_payload,
    )
    return 1, payload


def write_decision(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OrroNextError("ERR_ORRO_NEXT_WRITE_FAILED", str(exc)) from exc


def team_ledger_block_diagnostics(run_dir: Path) -> dict[str, Any] | None:
    """Lift runtime-reported lane blocks from the verifier verdict, if readable."""
    verdict, error = _load_optional_json(run_dir / "team-ledger-verdict.json")
    if error is not None or verdict is None:
        return None
    lane_results = verdict.get("lane_results")
    if not isinstance(lane_results, list):
        return None
    blocked_lanes = [
        {
            "lane_id": lane.get("lane_id"),
            "verification_state": lane.get("verification_state"),
            "blocked_reason": lane.get("blocked_reason"),
            "errors": lane.get("errors") if isinstance(lane.get("errors"), list) else [],
        }
        for lane in lane_results
        if isinstance(lane, dict) and lane.get("verification_state") == "blocked"
    ]
    if not blocked_lanes:
        return None
    verdict_path = shlex.quote(str(run_dir / "team-ledger-verdict.json"))
    return {
        "blocked_lane_count": len(blocked_lanes),
        "blocked_lanes": blocked_lanes,
        "diagnostic_command": (
            "jq '.decision, (.lane_results[]? ) | {lane_id, verification_state, "
            f"blocked_reason, errors}}' {verdict_path}"
        ),
    }


def _base_decision(
    run_dir: Path,
    *,
    decision: str,
    blocked: bool,
    reasons: list[str],
    home: Path,
    proofcheck_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed = _observed_artifacts(run_dir)
    return {
        "kind": CONTINUATION_KIND,
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "home": str(home),
        "decision": decision,
        "next_allowed": [],
        "blocked": blocked,
        "reasons": reasons,
        "observed_artifacts": observed,
        "role_status": _role_status(run_dir, observed, proofcheck_payload),
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


def _observed_artifacts(run_dir: Path) -> dict[str, bool]:
    return {key: (run_dir / filename).is_file() for key, filename in _ARTIFACT_FILES.items()}


def _role_status(
    run_dir: Path,
    observed: dict[str, bool],
    proofcheck_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    roles = _dispatch_roles(run_dir)
    if not roles:
        roles = [
            {"role_id": "runner", "phase": "proofrun"},
            {"role_id": "verifier", "phase": "proofcheck"},
            {"role_id": "handoff", "phase": "handoff"},
        ]
    result = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        phase = str(role.get("phase", ""))
        record = {
            "role_id": str(role.get("role_id", phase or "role")),
            "phase": phase,
            "status": _status_for_phase(phase, observed, proofcheck_payload),
            "evidence_refs": _evidence_refs_for_phase(phase, observed),
            "raises_assurance": False,
        }
        if isinstance(role.get("lane_ids"), list):
            record["lane_ids"] = role["lane_ids"]
        result.append(record)
    return result


def _dispatch_roles(run_dir: Path) -> list[Any]:
    payload, error = _load_optional_json(run_dir / "workflow-role-dispatch.json")
    if error is not None or payload is None:
        return []
    roles = payload.get("roles")
    return roles if isinstance(roles, list) else []


def _status_for_phase(
    phase: str,
    observed: dict[str, bool],
    proofcheck_payload: dict[str, Any] | None,
) -> str:
    if phase == "proofrun":
        return "executed" if observed["team_ledger"] else "pending"
    if phase == "proofcheck":
        if proofcheck_payload is None:
            return "pending"
        decision = proofcheck_payload.get("decision")
        if decision == "pass":
            return "verified"
        if decision in {"fail", "refuted"}:
            return "refuted"
        return "blocked"
    if phase == "handoff":
        return "packaged" if observed["handoff"] else "pending"
    return "observed" if observed["workflow_role_dispatch"] else "planned"


def _evidence_refs_for_phase(phase: str, observed: dict[str, bool]) -> list[str]:
    refs = []
    if phase == "proofrun":
        for key in ("team_ledger", "role_lane_plan", "workflow_role_dispatch"):
            if observed[key]:
                refs.append(_ARTIFACT_FILES[key])
    elif phase == "proofcheck":
        if observed["proofcheck_verdict"]:
            refs.append("proofcheck-verdict.json")
    elif phase == "handoff" and observed["handoff"]:
        refs.append("orro-handoff.json")
    return refs


def _proofcheck_state(
    run_dir: Path,
    proofcheck_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if proofcheck_payload is None:
        return {"decision": None, "error": None, "reason": None}
    decision = proofcheck_payload.get("decision")
    if decision != "pass":
        return {"decision": decision, "error": None, "reason": None}
    binding = proofcheck_payload.get("orro_binding")
    if not isinstance(binding, dict):
        return {
            "decision": decision,
            "error": {
                "code": ERR_ORRO_NEXT_PROOFCHECK_UNBOUND,
                "message": "proofcheck-verdict.json must include an ORRO proofcheck binding",
            },
            "reason": "proofcheck-verdict.json is not bound to this run",
        }
    expected = _proofcheck_binding(run_dir)
    if binding != expected:
        return {
            "decision": decision,
            "error": {
                "code": ERR_ORRO_NEXT_PROOFCHECK_BINDING_MISMATCH,
                "message": "proofcheck-verdict.json does not match this run directory",
            },
            "reason": "proofcheck-verdict.json binding does not match this run directory",
        }
    return {"decision": decision, "error": None, "reason": None}


def _handoff_state(
    run_dir: Path,
    handoff_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if handoff_payload is None:
        return {
            "error": {
                "code": ERR_ORRO_NEXT_HANDOFF_LOAD_FAILED,
                "message": "orro-handoff.json could not be loaded",
            },
            "reason": "orro-handoff.json could not be loaded",
        }
    if handoff_payload.get("kind") != "orro-handoff":
        return {
            "error": {
                "code": ERR_ORRO_NEXT_HANDOFF_UNBOUND,
                "message": "orro-handoff.json must have kind orro-handoff",
            },
            "reason": "orro-handoff.json is not an ORRO handoff artifact",
        }
    if handoff_payload.get("evidence_dir") != str(run_dir):
        return {
            "error": {
                "code": ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH,
                "message": "orro-handoff.json does not match this run directory",
            },
            "reason": "orro-handoff.json evidence_dir does not match this run directory",
        }
    expected_proofcheck_hash = _hash_file(run_dir / "proofcheck-verdict.json")
    for ref in handoff_payload.get("decision_refs", []):
        if not isinstance(ref, dict):
            continue
        if ref.get("path") != "proofcheck-verdict.json":
            continue
        if ref.get("sha256") == expected_proofcheck_hash and ref.get("decision") == "pass":
            return {"error": None, "reason": None}
        return {
            "error": {
                "code": ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH,
                "message": "orro-handoff.json proofcheck reference is stale",
            },
            "reason": "orro-handoff.json proofcheck reference does not match current verdict",
        }
    return {
        "error": {
            "code": ERR_ORRO_NEXT_HANDOFF_UNBOUND,
            "message": "orro-handoff.json must reference proofcheck-verdict.json",
        },
        "reason": "orro-handoff.json is not bound to proofcheck-verdict.json",
    }


def _proofcheck_binding(run_dir: Path) -> dict[str, Any]:
    return {
        "kind": "orro-proofcheck-binding",
        "schema_version": "1.0",
        "evidence_dir": str(run_dir),
        "artifact_hashes": _collect_artifact_hashes(run_dir),
    }


def _collect_artifact_hashes(run_dir: Path) -> list[dict[str, str]]:
    generated_names = {
        "orro-continuation-decision.json",
        "orro-handoff.json",
        "proofcheck-verdict.json",
        "team-ledger-verdict.json",
    }
    artifact_hashes = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        if path.name in generated_names:
            continue
        artifact_hashes.append(
            {
                "path": str(path.relative_to(run_dir)),
                "sha256": _hash_file(path),
            }
        )
    return artifact_hashes


def _load_optional_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"failed to read {path.name}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{path.name} must be a JSON object"
    return payload, None


def _critical_artifact_error(run_dir: Path) -> str | None:
    for filename in _CRITICAL_JSON_ARTIFACTS:
        _payload, error = _load_optional_json(run_dir / filename)
        if error is not None:
            return error
    return None


def _looks_like_partial_orro_dir(observed: dict[str, bool]) -> bool:
    return any(
        observed[key]
        for key in (
            "workflow_plan",
            "workflow_plan_binding",
            "role_lane_plan",
            "role_lane_plan_binding",
            "workflow_role_dispatch",
        )
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
