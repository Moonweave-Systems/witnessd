#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "orro-reality-check" / "manifest.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_SCENARIOS = {
    "trivial-doc-fix",
    "docs-change",
    "code-change",
    "review-only",
    "verification-only",
    "release-readiness",
    "risky-change",
    "scout-only-blocked",
    "stale-verdict-blocked",
}

REQUIRED_SCENARIO_FIELDS = {
    "name",
    "goal",
    "expected_task_class",
    "expected_profile",
    "expected_effort",
    "should_recommend_proofrun",
    "should_recommend_proofcheck",
    "should_recommend_handoff_without_proofcheck",
    "human_review_required",
    "success_criteria",
}


def _load_manifest(errors: list[str]) -> dict[str, Any]:
    if not MANIFEST.is_file():
        errors.append(f"missing scenario manifest: {MANIFEST.relative_to(ROOT)}")
        return {}
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"scenario manifest is invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("scenario manifest root must be a JSON object")
        return {}
    return payload


def _phases(decision: dict[str, Any]) -> list[str]:
    path = decision.get("recommended_path")
    if not isinstance(path, list):
        return []
    phases: list[str] = []
    for step in path:
        if isinstance(step, dict) and isinstance(step.get("phase"), str):
            phases.append(step["phase"])
    return phases


def _skipped_actions(decision: dict[str, Any]) -> set[str]:
    actions = decision.get("actions_to_skip")
    if not isinstance(actions, list):
        return set()
    return {
        item["action"]
        for item in actions
        if isinstance(item, dict) and isinstance(item.get("action"), str)
    }


def _validate_boundary(payload: dict[str, Any], errors: list[str]) -> None:
    boundary = payload.get("boundary")
    if not isinstance(boundary, dict):
        errors.append("manifest boundary must be an object")
        return
    expected = {
        "executes_commands": False,
        "verifies_evidence": False,
        "approves_merge": False,
        "raises_assurance": False,
        "depone_verifies": True,
        "witnessd_executes": True,
        "orro_exposes_workflow": True,
    }
    for key, value in expected.items():
        if boundary.get(key) is not value:
            errors.append(f"manifest boundary {key} must be {value}")


def _validate_scenario_shape(scenario: object, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(scenario, dict):
        errors.append("scenario entries must be JSON objects")
        return None
    name = scenario.get("name")
    label = name if isinstance(name, str) else "<unnamed>"
    for field in REQUIRED_SCENARIO_FIELDS:
        if field not in scenario:
            errors.append(f"scenario {label} missing field: {field}")
    if not isinstance(scenario.get("name"), str) or not scenario["name"]:
        errors.append("scenario name must be a non-empty string")
    if not isinstance(scenario.get("goal"), str) or not scenario["goal"]:
        errors.append(f"scenario {label} goal must be a non-empty string")
    if not isinstance(scenario.get("success_criteria"), list) or not scenario["success_criteria"]:
        errors.append(f"scenario {label} success_criteria must be a non-empty list")
    for field in [
        "should_recommend_proofrun",
        "should_recommend_proofcheck",
        "should_recommend_handoff_without_proofcheck",
        "human_review_required",
    ]:
        if not isinstance(scenario.get(field), bool):
            errors.append(f"scenario {label} {field} must be boolean")
    return scenario


def _validate_against_advise(scenario: dict[str, Any], errors: list[str]) -> None:
    from witnessd.orro_workstyle import advise_workstyle

    name = scenario["name"]
    decision = advise_workstyle(scenario["goal"], repo=ROOT, home=ROOT / ".witnessd")
    phases = _phases(decision)
    skipped = _skipped_actions(decision)

    expected_pairs = {
        "task_class": scenario["expected_task_class"],
        "recommended_profile": scenario["expected_profile"],
        "recommended_effort": scenario["expected_effort"],
        "human_review_required": scenario["human_review_required"],
    }
    for field, expected in expected_pairs.items():
        if decision.get(field) != expected:
            errors.append(
                f"scenario {name} expected {field}={expected!r}, got {decision.get(field)!r}"
            )

    proofrun_present = "proofrun" in phases
    proofcheck_present = "proofcheck" in phases
    if proofrun_present != scenario["should_recommend_proofrun"]:
        errors.append(f"scenario {name} proofrun recommendation mismatch")
    if proofcheck_present != scenario["should_recommend_proofcheck"]:
        errors.append(f"scenario {name} proofcheck recommendation mismatch")

    if "handoff" in phases:
        proofcheck_before_handoff = "proofcheck" in phases[: phases.index("handoff")]
        if not proofcheck_before_handoff and not scenario["should_recommend_handoff_without_proofcheck"]:
            errors.append(f"scenario {name} recommends handoff without prior proofcheck")

    if name == "trivial-doc-fix" and "role-lane team execution" not in skipped:
        errors.append("trivial-doc-fix must skip unnecessary role-lane team execution")
    if name == "trivial-doc-fix" and len(decision["recommended_path"]) >= 2:
        errors.append("trivial-doc-fix must recommend fewer than two workflow steps")
    if name == "risky-change" and "auto proofrun" not in skipped:
        errors.append("risky-change must skip auto proofrun")

    boundary = decision.get("boundary")
    if not isinstance(boundary, dict):
        errors.append(f"scenario {name} decision boundary missing")
        return
    for key in ["executes_commands", "verifies_evidence", "approves_merge", "raises_assurance"]:
        if boundary.get(key) is not False:
            errors.append(f"scenario {name} decision boundary {key} must be false")


def check() -> list[str]:
    errors: list[str] = []
    payload = _load_manifest(errors)
    if not payload:
        return errors

    if payload.get("kind") != "orro-product-reality-check-manifest":
        errors.append("manifest kind must be orro-product-reality-check-manifest")
    if payload.get("schema_version") != "0.1":
        errors.append("manifest schema_version must be 0.1")
    _validate_boundary(payload, errors)

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        errors.append("manifest scenarios must be a list")
        return errors

    seen: set[str] = set()
    for raw_scenario in scenarios:
        scenario = _validate_scenario_shape(raw_scenario, errors)
        if scenario is None:
            continue
        name = scenario.get("name")
        if isinstance(name, str):
            if name in seen:
                errors.append(f"duplicate scenario: {name}")
            seen.add(name)
        if REQUIRED_SCENARIO_FIELDS.issubset(scenario.keys()):
            _validate_against_advise(scenario, errors)

    missing = REQUIRED_SCENARIOS - seen
    for name in sorted(missing):
        errors.append(f"manifest missing required scenario: {name}")

    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"check_orro_product_reality: {error}", file=sys.stderr)
        return 1
    print("check_orro_product_reality: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
