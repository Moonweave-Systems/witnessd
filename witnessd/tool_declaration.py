"""Tool allowlist declaration advisory artifact."""

from __future__ import annotations

from typing import Any

from witnessd.claim import (
    Claim,
    ClaimEffect,
    ClaimFreshness,
    ClaimIntegrity,
    ClaimObservation,
    producer_declaration_boundary,
)


TOOL_DECLARATION_KIND = "moonweave-tool-declaration"
TOOL_DECLARATION_SCHEMA_VERSION = "1.0"
ENFORCEMENT_ENFORCED = "enforced"
USAGE_CONFIRMED = "verified"
USAGE_ENFORCED_ONLY = "enforced-only"


def normalize_tool_grant(tools: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "mcp": _string_list(tools.get("mcp", []), field="tools.mcp"),
        "allow": _string_list(tools.get("allow", []), field="tools.allow"),
    }


def build_tool_declaration(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    adapter: str,
    declared_tools: dict[str, Any],
    observed_tool_uses: list[dict[str, Any]] | None = None,
    usage_verification_status: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return _render_tool_declaration(
        build_tool_claim(
            role_id=role_id,
            lane_id=lane_id,
            capability=capability,
            adapter=adapter,
            declared_tools=declared_tools,
            observed_tool_uses=observed_tool_uses,
            usage_verification_status=usage_verification_status,
            detail=detail,
        )
    )


def build_tool_claim(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    adapter: str,
    declared_tools: dict[str, Any],
    observed_tool_uses: list[dict[str, Any]] | None = None,
    usage_verification_status: str | None = None,
    detail: str | None = None,
) -> Claim:
    normalized_tools = normalize_tool_grant(declared_tools)
    observed = list(observed_tool_uses or [])
    status = (
        usage_verification_status
        if usage_verification_status is not None
        else USAGE_CONFIRMED
        if observed
        else USAGE_ENFORCED_ONLY
    )
    return Claim.from_producer(
        value={
            "role_id": role_id,
            "lane_id": lane_id,
            "capability": capability,
            "adapter": adapter,
            "declared_tools": normalized_tools,
            "status": status,
            "observed": observed,
            "detail": detail,
        },
        observation=(
            ClaimObservation.OBSERVED if observed else ClaimObservation.MISSING
        ),
        integrity=ClaimIntegrity.UNBOUND,
        effect=ClaimEffect.ADVISORY,
        freshness=ClaimFreshness.CURRENT if observed else ClaimFreshness.PENDING,
    )


def _render_tool_declaration(claim: Claim) -> dict[str, Any]:
    value = claim.value
    if not isinstance(value, dict):
        raise TypeError("tool claim value must be a dictionary")
    return {
        "kind": TOOL_DECLARATION_KIND,
        "schema_version": TOOL_DECLARATION_SCHEMA_VERSION,
        **producer_declaration_boundary(claim),
        "role_id": value["role_id"],
        "lane_id": value["lane_id"],
        "capability": value["capability"],
        "adapter": value["adapter"],
        "declared_tools": value["declared_tools"],
        "enforcement_status": ENFORCEMENT_ENFORCED,
        "usage_verification_status": value["status"],
        "observed_tool_uses": value["observed"],
        "detail": value["detail"],
    }


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a string list")
    return list(value)
