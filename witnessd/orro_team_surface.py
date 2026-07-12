"""Usable ORRO team onboarding helpers.

These helpers create readiness configuration and prepare existing ORRO
role-lane plans for execution. They do not execute lanes, verify evidence, or
raise assurance.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from witnessd.role_capability import (
    DEFAULT_DEVELOPER_ROLEPACK,
    ROLEPACK_KIND,
    ROLEPACK_SCHEMA_VERSION,
    ROLE_CAPABILITY_SCHEMA_VERSION,
    validate_rolepack,
)


PLACEHOLDER_PROMPT_PREFIX = "Execute ORRO role "


class OrroTeamSurfaceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def build_rolepack_scaffold(
    *,
    template: str | None = "developer",
    roles: list[str] | None = None,
    write_scope: list[str] | None = None,
    tool_mcp: list[str] | None = None,
    tool_allow: list[str] | None = None,
) -> dict[str, Any]:
    if template not in {None, "developer"}:
        raise OrroTeamSurfaceError(
            "ERR_ORRO_TEAM_TEMPLATE_UNKNOWN", f"unknown team template: {template}"
        )
    if roles:
        rolepack = _rolepack_from_role_specs(roles)
    else:
        rolepack = copy.deepcopy(DEFAULT_DEVELOPER_ROLEPACK)
    rolepack["name"] = "custom-team" if roles else "developer"
    _apply_execute_defaults(
        rolepack,
        write_scope=write_scope,
        tool_mcp=tool_mcp,
        tool_allow=tool_allow,
    )
    validate_rolepack(rolepack)
    return rolepack


def write_rolepack_scaffold(
    path: Path,
    rolepack: dict[str, Any],
    *,
    yes: bool = False,
) -> dict[str, Any]:
    existed = path.exists()
    if path.exists() and not yes:
        raise OrroTeamSurfaceError(
            "ERR_ORRO_TEAM_INIT_EXISTS",
            f"{path} already exists; pass --yes to overwrite",
        )
    validate_rolepack(rolepack)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rolepack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "kind": "orro-team-init-result",
        "status": "overwritten" if existed else "created",
        "path": str(path),
        "rolepack": {
            "kind": ROLEPACK_KIND,
            "schema_version": ROLEPACK_SCHEMA_VERSION,
            "name": rolepack["name"],
            "grant_count": len(rolepack["grants"]),
        },
        "can_change_evidence_verdict": False,
        "boundary": "readiness configuration only; not execution, not proof, not assurance",
    }


def apply_task_prompt_to_role_lane_plan(
    role_lane_plan: dict[str, Any],
    *,
    task: str,
) -> dict[str, Any]:
    patched = copy.deepcopy(role_lane_plan)
    placeholder_count = 0
    patched_count = 0
    region_patched_count = 0
    lanes = patched.get("lanes")
    if not isinstance(lanes, list):
        return {
            "role_lane_plan": patched,
            "patched_count": 0,
            "placeholder_count": 0,
            "region_patched_count": 0,
        }
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        prompt = lane.get("prompt")
        if isinstance(prompt, str) and prompt.startswith(PLACEHOLDER_PROMPT_PREFIX):
            placeholder_count += 1
        if lane.get("phase") == "proofrun" and lane.get("may_execute") is True:
            lane["prompt"] = task
            patched_count += 1
            exact_scope = _exact_write_scope(lane.get("granted_write_scope"))
            if exact_scope:
                lane["region"] = exact_scope
                region_patched_count += 1
    return {
        "role_lane_plan": patched,
        "patched_count": patched_count,
        "placeholder_count": placeholder_count,
        "region_patched_count": region_patched_count,
    }


def verdict_has_no_work_error(payload: Any) -> bool:
    if isinstance(payload, dict):
        code = payload.get("code")
        if code == "ERR_TEAM_LEDGER_TOUCHED_FILES_REQUIRED":
            return True
        return any(verdict_has_no_work_error(value) for value in payload.values())
    if isinstance(payload, list):
        return any(verdict_has_no_work_error(value) for value in payload)
    return False


def _rolepack_from_role_specs(role_specs: list[str]) -> dict[str, Any]:
    grants = []
    for spec in role_specs:
        parts = spec.split(":")
        if len(parts) not in {2, 3} or not parts[0] or not parts[1]:
            raise OrroTeamSurfaceError(
                "ERR_ORRO_TEAM_ROLE_FORMAT",
                "--role must be role_id:adapter[:model]",
            )
        role_id, adapter = parts[0], parts[1]
        capability = "review" if role_id == "reviewer" else "execute"
        grant: dict[str, Any] = {
            "schema_version": ROLE_CAPABILITY_SCHEMA_VERSION,
            "role_id": role_id,
            "capability": capability,
            "adapters": [adapter],
            "write_scope": [] if capability == "review" else ["orro/**", "docs/**"],
            "tools": {"mcp": [], "allow": []},
        }
        if len(parts) == 3 and parts[2]:
            grant["model"] = parts[2]
        grants.append(grant)
    return {
        "kind": ROLEPACK_KIND,
        "schema_version": ROLEPACK_SCHEMA_VERSION,
        "name": "custom-team",
        "grants": grants,
    }


def _apply_execute_defaults(
    rolepack: dict[str, Any],
    *,
    write_scope: list[str] | None,
    tool_mcp: list[str] | None,
    tool_allow: list[str] | None,
) -> None:
    for grant in rolepack.get("grants", []):
        if not isinstance(grant, dict) or grant.get("capability") != "execute":
            continue
        if write_scope is not None:
            grant["write_scope"] = list(write_scope)
        grant["tools"] = {
            "mcp": list(tool_mcp or grant.get("tools", {}).get("mcp", [])),
            "allow": list(tool_allow or grant.get("tools", {}).get("allow", [])),
        }


def _exact_write_scope(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    exact = [
        item
        for item in value
        if isinstance(item, str)
        and item
        and not any(marker in item for marker in ("*", "?", "[", "]"))
    ]
    return exact
