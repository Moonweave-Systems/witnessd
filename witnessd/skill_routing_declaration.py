"""Human-facing role capability skill-routing declaration artifacts."""

from __future__ import annotations

import fnmatch
from typing import Any

from witnessd.claim import (
    Claim,
    ClaimEffect,
    ClaimFreshness,
    ClaimIntegrity,
    ClaimObservation,
    producer_declaration_boundary,
)


def normalize_skill_routing_declaration(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("skill_routing must be a JSON object")
    unknown = set(value) - {
        "forbidden_skills",
        "preferred_skills",
        "enforcement",
        "reason",
    }
    if unknown:
        raise ValueError(
            "skill_routing has unsupported fields: " + ", ".join(sorted(unknown))
        )
    forbidden = _string_list(value.get("forbidden_skills", []), "forbidden_skills")
    preferred = _string_list(value.get("preferred_skills", []), "preferred_skills")
    enforcement = value.get("enforcement", "block")
    if enforcement not in {"block", "advisory"}:
        raise ValueError("skill_routing enforcement must be block or advisory")
    result: dict[str, Any] = {
        "forbidden_skills": forbidden,
        "preferred_skills": preferred,
        "enforcement": enforcement,
    }
    reason = value.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or not reason:
            raise ValueError("skill_routing reason must be a non-empty string")
        result["reason"] = reason
    return result


def build_skill_routing_declaration(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    skill_routing: dict[str, Any],
    observed_skills: list[str],
) -> dict[str, Any]:
    return _render_skill_routing_declaration(
        build_skill_routing_claim(
            role_id=role_id,
            lane_id=lane_id,
            capability=capability,
            skill_routing=skill_routing,
            observed_skills=observed_skills,
        )
    )


def build_skill_routing_claim(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    skill_routing: dict[str, Any],
    observed_skills: list[str],
) -> Claim:
    forbidden = list(skill_routing.get("forbidden_skills", []))
    preferred = list(skill_routing.get("preferred_skills", []))
    enforcement = str(skill_routing.get("enforcement", "block"))
    violations = [
        skill for skill in observed_skills if _matches_any_pattern(skill, forbidden)
    ]
    conformance = "fail" if violations and enforcement == "block" else "pass"
    if violations and enforcement == "advisory":
        conformance = "advisory-fail"
    return Claim.from_producer(
        value={
            "role_id": role_id,
            "lane_id": lane_id,
            "capability": capability,
            "forbidden": forbidden,
            "preferred": preferred,
            "enforcement": enforcement,
            "observed_skills": list(observed_skills),
            "conformance": conformance,
            "reason": skill_routing.get("reason"),
        },
        observation=ClaimObservation.OBSERVED,
        integrity=ClaimIntegrity.UNBOUND,
        effect=ClaimEffect.ADVISORY,
        freshness=ClaimFreshness.CURRENT,
    )


def _render_skill_routing_declaration(claim: Claim) -> dict[str, Any]:
    value = claim.value
    if not isinstance(value, dict):
        raise TypeError("skill-routing claim value must be a dictionary")
    declaration = {
        "kind": "moonweave-skill-routing-declaration",
        "schema_version": "v110.role_capability_skill_routing",
        "role_id": value["role_id"],
        "lane_id": value["lane_id"],
        "capability": value["capability"],
        **producer_declaration_boundary(claim),
        "declared_forbidden": value["forbidden"],
        "declared_preferred": value["preferred"],
        "enforcement": value["enforcement"],
        "observed_skills": [
            {
                "skill": skill,
                "evidence_marker": "observed raw provider event matched skill path or explicit skill declaration",
            }
            for skill in value["observed_skills"]
        ],
        "conformance": value["conformance"],
    }
    if isinstance(value["reason"], str):
        declaration["reason"] = value["reason"]
    return declaration


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"skill_routing {field} must be a string list")
    return list(value)


def _matches_any_pattern(skill: str, patterns: list[str]) -> bool:
    return any(skill == pattern or fnmatch.fnmatchcase(skill, pattern) for pattern in patterns)
