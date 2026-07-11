"""Role capability grants for ORRO role-lane planning.

S1 intentionally models only role, capability, adapters, and model policy
reference. Tool and write-scope grants are later slices and must not be
accepted silently here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ROLEPACK_KIND = "moonweave-rolepack"
ROLEPACK_SCHEMA_VERSION = "0.1"
ROLE_CAPABILITY_SCHEMA_VERSION = "0.1"
ROLE_CAPABILITY_CAPABILITIES = ("execute", "review")
ROLE_CAPABILITY_ADAPTERS = ("shell", "codex", "claude", "agy", "gemini", "opencode")

_GRANT_FIELDS = {
    "schema_version",
    "role_id",
    "capability",
    "adapters",
    "model_policy_ref",
}
_ROLEPACK_FIELDS = {"kind", "schema_version", "name", "grants"}


@dataclass(frozen=True)
class RoleCapabilityGrant:
    role_id: str
    capability: str
    adapters: tuple[str, ...]
    model_policy_ref: str
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
        model_policy_ref = payload.get("model_policy_ref")
        adapters = payload.get("adapters")
        if not isinstance(role_id, str) or not role_id:
            raise ValueError("role capability grant role_id is invalid")
        if capability not in ROLE_CAPABILITY_CAPABILITIES:
            raise ValueError("role capability grant capability is invalid")
        if not isinstance(model_policy_ref, str) or not model_policy_ref:
            raise ValueError("role capability grant model_policy_ref is invalid")
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
        return cls(
            role_id=role_id,
            capability=str(capability),
            adapters=tuple(adapters),
            model_policy_ref=model_policy_ref,
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "capability": self.capability,
            "adapters": list(self.adapters),
            "model_policy_ref": self.model_policy_ref,
        }


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
            "model_policy_ref": "default",
        },
        {
            "schema_version": ROLE_CAPABILITY_SCHEMA_VERSION,
            "role_id": "reviewer",
            "capability": "review",
            "adapters": ["agy", "gemini"],
            "model_policy_ref": "default",
        },
    ],
}


def validate_rolepack(rolepack: dict[str, Any]) -> None:
    _rolepack_grants(rolepack)


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
