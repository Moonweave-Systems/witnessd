"""Write-scope declaration advisory artifact."""

from __future__ import annotations

import fnmatch
from typing import Any


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
) -> dict[str, Any]:
    conforms = write_scope_allows_paths(touched_files, declared_write_scope)
    return {
        "kind": WRITE_SCOPE_DECLARATION_KIND,
        "schema_version": WRITE_SCOPE_DECLARATION_SCHEMA_VERSION,
        "can_change_evidence_verdict": False,
        "role_id": role_id,
        "lane_id": lane_id,
        "capability": capability,
        "declared_write_scope": list(declared_write_scope),
        "allowed_touched_files": list(allowed_touched_files),
        "touched_files": list(touched_files),
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
