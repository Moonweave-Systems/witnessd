"""Write-scope declaration advisory artifact."""

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


WRITE_SCOPE_DECLARATION_KIND = "moonweave-write-scope-declaration"
WRITE_SCOPE_DECLARATION_SCHEMA_VERSION = "1.0"
VERIFICATION_CONFIRMED = "verified"
VERIFICATION_REJECTED = "rejected"


def write_scope_allows_paths(paths: list[str], write_scope: list[str]) -> bool:
    return all(_path_allowed(path, write_scope) for path in paths)


def build_write_scope_declaration(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    declared_write_scope: list[str],
    allowed_touched_files: list[str],
    touched_files: list[str],
    conforms: bool | None = None,
    emitted_values_redacted: bool = False,
) -> dict[str, Any]:
    return _render_write_scope_declaration(
        build_write_scope_claim(
            role_id=role_id,
            lane_id=lane_id,
            capability=capability,
            declared_write_scope=declared_write_scope,
            allowed_touched_files=allowed_touched_files,
            touched_files=touched_files,
            conforms=conforms,
            emitted_values_redacted=emitted_values_redacted,
        )
    )


def build_write_scope_claim(
    *,
    role_id: str,
    lane_id: str,
    capability: str,
    declared_write_scope: list[str],
    allowed_touched_files: list[str],
    touched_files: list[str],
    conforms: bool | None = None,
    emitted_values_redacted: bool = False,
) -> Claim:
    if conforms is None:
        conforms = write_scope_allows_paths(touched_files, declared_write_scope)
    return Claim.from_producer(
        value={
            "role_id": role_id,
            "lane_id": lane_id,
            "capability": capability,
            "declared_write_scope": list(declared_write_scope),
            "allowed_touched_files": list(allowed_touched_files),
            "touched_files": list(touched_files),
            "emitted_values_redacted": emitted_values_redacted,
            "conforms": conforms,
        },
        observation=ClaimObservation.OBSERVED,
        integrity=ClaimIntegrity.UNBOUND,
        effect=ClaimEffect.ADVISORY,
        freshness=ClaimFreshness.CURRENT,
    )


def _render_write_scope_declaration(claim: Claim) -> dict[str, Any]:
    value = claim.value
    if not isinstance(value, dict):
        raise TypeError("write-scope claim value must be a dictionary")
    conforms = bool(value["conforms"])
    return {
        "kind": WRITE_SCOPE_DECLARATION_KIND,
        "schema_version": WRITE_SCOPE_DECLARATION_SCHEMA_VERSION,
        **producer_declaration_boundary(claim),
        "role_id": value["role_id"],
        "lane_id": value["lane_id"],
        "capability": value["capability"],
        "declared_write_scope": value["declared_write_scope"],
        "allowed_touched_files": value["allowed_touched_files"],
        "touched_files": value["touched_files"],
        "conformance_evaluation": {
            "emitted_scope_and_path_values": (
                "redacted" if value["emitted_values_redacted"] else "unredacted"
            ),
            "evaluated_on": "unredacted scope and path values",
        },
        "verification_status": VERIFICATION_CONFIRMED
        if conforms
        else VERIFICATION_REJECTED,
        "conformance": "pass" if conforms else "fail",
        "detail": None
        if conforms
        else "touched_files are not a subset of declared_write_scope",
    }


def _path_allowed(path: str, write_scope: list[str]) -> bool:
    return any(path == pattern or fnmatch.fnmatchcase(path, pattern) for pattern in write_scope)
