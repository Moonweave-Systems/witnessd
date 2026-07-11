"""Tool allowlist declaration advisory artifact."""

from __future__ import annotations

from typing import Any


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
    normalized_tools = normalize_tool_grant(declared_tools)
    observed = list(observed_tool_uses or [])
    status = (
        usage_verification_status
        if usage_verification_status is not None
        else USAGE_CONFIRMED
        if observed
        else USAGE_ENFORCED_ONLY
    )
    return {
        "kind": TOOL_DECLARATION_KIND,
        "schema_version": TOOL_DECLARATION_SCHEMA_VERSION,
        "can_change_evidence_verdict": False,
        "role_id": role_id,
        "lane_id": lane_id,
        "capability": capability,
        "adapter": adapter,
        "declared_tools": normalized_tools,
        "enforcement_status": ENFORCEMENT_ENFORCED,
        "usage_verification_status": status,
        "observed_tool_uses": observed,
        "detail": detail,
    }


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a string list")
    return list(value)
