"""Capture privacy profiles for evidence artifacts."""

from __future__ import annotations

import hashlib
import re
from typing import Any

REDACTION_MANIFEST_ARTIFACT_NAME = "redaction-manifest.json"
REDACTION_MANIFEST_SUBJECT_NAME = "redaction-manifest"
REDACTION_MANIFEST_KIND = "moonweave-redaction-manifest"
REDACTION_MANIFEST_SCHEMA_VERSION = "1.0"
CAPTURE_PROFILE_FULL = "full"
CAPTURE_PROFILE_REDACTED = "redacted"
SECRET_SCRUB_BOUNDARY = {
    "best_effort": True,
    "guarantees_completeness": False,
    "note": "high-confidence secret patterns only; not a guarantee that all secrets are removed",
}

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_pat_classic", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github_pat_fine", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}"),
    ),
)
_SECRET_RULE_ORDER = {
    rule: index for index, (rule, _pattern) in enumerate(_SECRET_PATTERNS)
}


def validate_capture_profile(profile: str) -> str:
    if profile not in {CAPTURE_PROFILE_FULL, CAPTURE_PROFILE_REDACTED}:
        raise ValueError("capture_profile must be 'full' or 'redacted'")
    return profile


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def merge_secret_findings(
    *finding_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for findings in finding_groups:
        for finding in findings:
            rule = finding.get("rule")
            match_sha256 = finding.get("match_sha256")
            count = finding.get("count")
            if (
                isinstance(rule, str)
                and isinstance(match_sha256, str)
                and isinstance(count, int)
                and count > 0
            ):
                key = (rule, match_sha256)
                counts[key] = counts.get(key, 0) + count
    return [
        {"rule": rule, "match_sha256": match_sha256, "count": count}
        for (rule, match_sha256), count in sorted(
            counts.items(),
            key=lambda item: (
                _SECRET_RULE_ORDER.get(item[0][0], len(_SECRET_RULE_ORDER)),
                item[0][1],
            ),
        )
    ]


def redact_secrets(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Best-effort scrub of the fixed high-confidence secret pattern set."""
    scrubbed = text
    findings: list[dict[str, Any]] = []
    for rule, pattern in _SECRET_PATTERNS:
        rule_findings: list[dict[str, Any]] = []

        def replace(match: re.Match[str]) -> str:
            raw_match = match.group(0)
            prefix = ""
            matched_value = raw_match
            if rule == "bearer_token":
                separator = re.search(r"\s+", raw_match)
                if separator is not None:
                    prefix = raw_match[: separator.end()]
                    matched_value = raw_match[separator.end() :]
            match_sha256 = _sha256_text(matched_value)
            rule_findings.append(
                {
                    "rule": rule,
                    "match_sha256": match_sha256,
                    "count": 1,
                }
            )
            return f"{prefix}[REDACTED:{rule}:{match_sha256[:12]}]"

        scrubbed = pattern.sub(replace, scrubbed)
        findings = merge_secret_findings(findings, rule_findings)
    return scrubbed, findings


def redact_secrets_in(value: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Recursively scrub strings and merge findings without retaining raw values."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        scrubbed_items = []
        findings: list[dict[str, Any]] = []
        for item in value:
            scrubbed_item, item_findings = redact_secrets_in(item)
            scrubbed_items.append(scrubbed_item)
            findings = merge_secret_findings(findings, item_findings)
        return scrubbed_items, findings
    if isinstance(value, dict):
        scrubbed_mapping = {}
        findings = []
        for key, item in value.items():
            scrubbed_item, item_findings = redact_secrets_in(item)
            scrubbed_mapping[key] = scrubbed_item
            findings = merge_secret_findings(findings, item_findings)
        return scrubbed_mapping, findings
    return value, []


def build_secret_scrub_manifest(
    *,
    run_id: str,
    capture_profile: str,
    findings: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Add the honest scrub boundary, emitting full-profile metadata only on match."""
    existing = manifest.get("secret_scrub") if isinstance(manifest, dict) else None
    existing_findings = (
        existing.get("rules_matched") if isinstance(existing, dict) else None
    )
    merged = merge_secret_findings(
        existing_findings if isinstance(existing_findings, list) else [], findings
    )
    if capture_profile != CAPTURE_PROFILE_REDACTED and not merged:
        return manifest
    result = dict(manifest or {})
    result.setdefault("kind", REDACTION_MANIFEST_KIND)
    result.setdefault("schema_version", REDACTION_MANIFEST_SCHEMA_VERSION)
    result.setdefault("run_id", run_id)
    result.setdefault("capture_profile", capture_profile)
    result["secret_scrub"] = {
        "rules_matched": merged,
        "boundary": dict(SECRET_SCRUB_BOUNDARY),
    }
    return result


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
