"""Role capability grants for ORRO role-lane planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLEPACK_KIND = "moonweave-rolepack"
ROLEPACK_SCHEMA_VERSION = "0.2"
ROLE_CAPABILITY_SCHEMA_VERSION = "0.2"
ROLE_CAPABILITY_CAPABILITIES = ("execute", "review")
ROLE_CAPABILITY_ADAPTERS = ("shell", "codex", "claude", "agy", "gemini", "opencode")

_GRANT_FIELDS = {
    "schema_version",
    "role_id",
    "capability",
    "adapters",
    "model",
    "write_scope",
    "tools",
}
_ROLEPACK_FIELDS = {"kind", "schema_version", "name", "grants"}


class RolepackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RoleCapabilityGrant:
    role_id: str
    capability: str
    adapters: tuple[str, ...]
    model: str | None = None
    write_scope: tuple[str, ...] | None = None
    tools: dict[str, tuple[str, ...]] | None = None
    schema_version: str = ROLE_CAPABILITY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RoleCapabilityGrant":
        if not isinstance(payload, dict):
            raise ValueError("role capability grant must be a JSON object")
        unknown = set(payload) - _GRANT_FIELDS
        if unknown:
            raise ValueError(
                "role capability grant has unsupported S1 fields: "
                + ", ".join(sorted(unknown))
            )
        schema_version = str(
            payload.get("schema_version", ROLE_CAPABILITY_SCHEMA_VERSION)
        )
        if schema_version != ROLE_CAPABILITY_SCHEMA_VERSION:
            raise ValueError("role capability grant schema_version is invalid")
        role_id = payload.get("role_id")
        capability = payload.get("capability")
        model = payload.get("model")
        adapters = payload.get("adapters")
        write_scope = payload.get("write_scope")
        tools = payload.get("tools")
        if not isinstance(role_id, str) or not role_id:
            raise ValueError("role capability grant role_id is invalid")
        if capability not in ROLE_CAPABILITY_CAPABILITIES:
            raise ValueError("role capability grant capability is invalid")
        if model is not None and (not isinstance(model, str) or not model):
            raise ValueError("role capability grant model is invalid")
        if (
            not isinstance(adapters, list)
            or not adapters
            or not all(isinstance(adapter, str) and adapter for adapter in adapters)
        ):
            raise ValueError("role capability grant adapters must be a string list")
        unknown_adapters = [
            adapter for adapter in adapters if adapter not in ROLE_CAPABILITY_ADAPTERS
        ]
        if unknown_adapters:
            raise ValueError(
                "role capability grant adapters are unsupported: "
                + ", ".join(sorted(unknown_adapters))
            )
        if write_scope is not None and (
            not isinstance(write_scope, list)
            or not all(isinstance(item, str) and item for item in write_scope)
        ):
            raise ValueError("role capability grant write_scope must be a string list")
        if tools is not None:
            _validate_tools(tools)
        return cls(
            role_id=role_id,
            capability=str(capability),
            adapters=tuple(adapters),
            model=model,
            write_scope=tuple(write_scope) if write_scope is not None else None,
            tools=(
                {
                    "mcp": tuple(tools.get("mcp", [])),
                    "allow": tuple(tools.get("allow", [])),
                }
                if tools is not None
                else None
            ),
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "capability": self.capability,
            "adapters": list(self.adapters),
        }
        if self.model is not None:
            payload["model"] = self.model
        if self.write_scope is not None:
            payload["write_scope"] = list(self.write_scope)
        if self.tools is not None:
            payload["tools"] = {
                "mcp": list(self.tools["mcp"]),
                "allow": list(self.tools["allow"]),
            }
        return payload


DEFAULT_DEVELOPER_ROLEPACK: dict[str, Any] = {
    "kind": ROLEPACK_KIND,
    "schema_version": ROLEPACK_SCHEMA_VERSION,
    "name": "developer",
    "grants": [
        {
            "schema_version": ROLE_CAPABILITY_SCHEMA_VERSION,
            "role_id": "runner",
            "capability": "execute",
            "adapters": ["shell", "codex", "claude", "opencode"],
            "write_scope": ["orro/**", "docs/**"],
            "tools": {"mcp": [], "allow": []},
        },
        {
            "schema_version": ROLE_CAPABILITY_SCHEMA_VERSION,
            "role_id": "reviewer",
            "capability": "review",
            "adapters": ["agy", "gemini"],
            "write_scope": [],
            "tools": {"mcp": [], "allow": []},
        },
    ],
}

ROLEPACK_REGISTRY: dict[str, dict[str, Any]] = {
    "developer": DEFAULT_DEVELOPER_ROLEPACK,
}


def _validate_tools(tools: Any) -> None:
    if not isinstance(tools, dict):
        raise ValueError("role capability grant tools must be an object")
    unknown = set(tools) - {"mcp", "allow"}
    if unknown:
        raise ValueError(
            "role capability grant tools has unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    for key in ("mcp", "allow"):
        value = tools.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise ValueError(f"role capability grant tools.{key} must be a string list")


def validate_rolepack(rolepack: dict[str, Any]) -> None:
    _rolepack_grants(rolepack)


def resolve_rolepack(name: str | None) -> dict[str, Any] | None:
    if name is None:
        return None
    rolepack = ROLEPACK_REGISTRY.get(name)
    if rolepack is None:
        raise RolepackError(
            "ERR_ORRO_ROLEPACK_UNKNOWN", f"unknown rolepack: {name}"
        )
    validate_rolepack(rolepack)
    return rolepack


def load_rolepack_file(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RolepackError("ERR_ORRO_ROLEPACK_LOAD_FAILED", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise RolepackError("ERR_ORRO_ROLEPACK_INVALID", str(exc)) from exc
    try:
        validate_rolepack(payload)
    except ValueError as exc:
        raise RolepackError("ERR_ORRO_ROLEPACK_INVALID", str(exc)) from exc
    return payload


def grant_for_role(
    rolepack: dict[str, Any], role_id: str
) -> RoleCapabilityGrant | None:
    for grant in _rolepack_grants(rolepack):
        if grant.role_id == role_id:
            return grant
    return None


def _rolepack_grants(rolepack: dict[str, Any]) -> tuple[RoleCapabilityGrant, ...]:
    if not isinstance(rolepack, dict):
        raise ValueError("rolepack must be a JSON object")
    unknown = set(rolepack) - _ROLEPACK_FIELDS
    if unknown:
        raise ValueError(
            "rolepack has unsupported S1 fields: " + ", ".join(sorted(unknown))
        )
    if rolepack.get("kind") != ROLEPACK_KIND:
        raise ValueError("rolepack kind is invalid")
    if rolepack.get("schema_version") != ROLEPACK_SCHEMA_VERSION:
        raise ValueError("rolepack schema_version is invalid")
    if not isinstance(rolepack.get("name"), str) or not rolepack.get("name"):
        raise ValueError("rolepack name is invalid")
    grants_payload = rolepack.get("grants")
    if not isinstance(grants_payload, list):
        raise ValueError("rolepack grants must be a list")
    grants = tuple(RoleCapabilityGrant.from_dict(grant) for grant in grants_payload)
    role_ids = [grant.role_id for grant in grants]
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("rolepack grants must not duplicate role_id")
    return grants
