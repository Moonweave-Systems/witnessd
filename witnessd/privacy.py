"""Capture privacy profiles for evidence artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

REDACTION_MANIFEST_ARTIFACT_NAME = "redaction-manifest.json"
REDACTION_MANIFEST_SUBJECT_NAME = "redaction-manifest"
REDACTION_MANIFEST_KIND = "moonweave-redaction-manifest"
REDACTION_MANIFEST_SCHEMA_VERSION = "1.0"
CAPTURE_PROFILE_FULL = "full"
CAPTURE_PROFILE_REDACTED = "redacted"


def validate_capture_profile(profile: str) -> str:
    if profile not in {CAPTURE_PROFILE_FULL, CAPTURE_PROFILE_REDACTED}:
        raise ValueError("capture_profile must be 'full' or 'redacted'")
    return profile


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token(surface: str, value: str) -> str:
    return f"{surface}:{_sha256_text(value)[:16]}"


def build_redaction_context(
    *,
    run_id: str,
    prompt: str,
    paths: list[str],
    worktree: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    replacements: dict[str, str] = {}
    path_records: list[dict[str, str]] = []
    for raw_path in [item for item in [*paths, worktree] if item]:
        if raw_path in replacements:
            continue
        token = _token("path", raw_path)
        replacements[raw_path] = token
        path_records.append(
            {
                "token": token,
                "sha256": _sha256_text(raw_path),
            }
        )

    env_records: list[dict[str, str]] = []
    for name, value in sorted((env or {}).items()):
        if not isinstance(value, str) or not value:
            continue
        if name not in {"CODEX_HOME"}:
            continue
        token = _token(f"env.{name}", value)
        replacements[value] = token
        env_records.append(
            {
                "name": name,
                "token": token,
                "value_sha256": _sha256_text(value),
            }
        )

    prompt_token = _token("prompt", prompt) if prompt else "prompt:empty"
    if prompt:
        replacements[prompt] = prompt_token

    manifest = {
        "kind": REDACTION_MANIFEST_KIND,
        "schema_version": REDACTION_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "capture_profile": CAPTURE_PROFILE_REDACTED,
        "prompt_token": prompt_token,
        "prompt_sha256": _sha256_text(prompt),
        "paths": path_records,
        "environment": env_records,
        "boundary": {
            "contains_raw_paths": False,
            "contains_raw_prompt": False,
            "contains_raw_env_values": False,
        },
    }
    return {"manifest": manifest, "replacements": replacements}


def redact_value(value: Any, context: dict[str, Any] | None) -> Any:
    if not context:
        return value
    replacements = context.get("replacements")
    if not isinstance(replacements, dict):
        return value
    if isinstance(value, str):
        redacted = value
        for raw, token in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            if isinstance(raw, str) and isinstance(token, str) and raw:
                redacted = redacted.replace(raw, token)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, context) for key, item in value.items()}
    return value
