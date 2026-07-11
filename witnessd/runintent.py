"""Run-intent artifact builder and signer.

The run-intent is the pre-execution control-plane declaration for a lane. It is
signed before provider execution, then included as a normal artifact subject in
the final evidence bundle so Depone can verify that the run's declared boundary
was present and content-addressed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from witnessd.signing import DEFAULT_OPERATOR_KEY_ID, sign_dsse

RUN_INTENT_ARTIFACT_NAME = "run-intent.json"
RUN_INTENT_SUBJECT_NAME = "run-intent"
RUN_INTENT_ARTIFACT_KIND = "moonweave-run-intent-artifact"
RUN_INTENT_SCHEMA_VERSION = "1.0"
RUN_INTENT_ROLE_CAPABILITY_SCHEMA_VERSION = "1.1"
ROLE_CAPABILITY_SCHEMA_VERSION = "1.0"
RUN_INTENT_PAYLOAD_TYPE = "application/vnd.moonweave.run-intent+json"
DEFAULT_ADAPTER_VERSION = "witnessd.adapter_run/1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def git_baseline(worktree: str) -> dict[str, Any]:
    repo = str(Path(worktree).resolve(strict=False))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v2", "-z"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "git_head": head.stdout.strip() if head.returncode == 0 else "unknown",
        "git_head_status": "known" if head.returncode == 0 else "unknown",
        "git_status_sha256": (
            hashlib.sha256(status.stdout).hexdigest() if status.returncode == 0 else None
        ),
        "git_status_state": "known" if status.returncode == 0 else "unknown",
    }


def build_run_intent(
    *,
    run_id: str,
    baseline: dict[str, Any],
    allowed_paths: list[str],
    approval_policy: str,
    sandbox_mode: str,
    provider: str,
    instruction_hashes: dict[str, str],
    budgets: dict[str, Any],
    capture_profile: str = "full",
    adapter_version: str = DEFAULT_ADAPTER_VERSION,
    role_capability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_version = (
        RUN_INTENT_ROLE_CAPABILITY_SCHEMA_VERSION
        if role_capability is not None
        else RUN_INTENT_SCHEMA_VERSION
    )
    intent = {
        "schema_version": schema_version,
        "run_id": run_id,
        "baseline": baseline,
        "allowed_paths": list(allowed_paths),
        "approval": {"policy": approval_policy},
        "sandbox": {"mode": sandbox_mode},
        "provider": {"name": provider, "adapter_version": adapter_version},
        "instruction_hashes": dict(instruction_hashes),
        "budgets": dict(budgets),
        "capture_profile": capture_profile,
    }
    if role_capability is not None:
        intent["role_capability"] = dict(role_capability)
    return intent


def sign_run_intent(
    intent: dict[str, Any],
    private_key_path: str,
    *,
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
) -> dict[str, Any]:
    payload = _canonical_json(intent).encode("utf-8")
    envelope = {
        "payloadType": RUN_INTENT_PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [],
    }
    return {
        "kind": RUN_INTENT_ARTIFACT_KIND,
        "schema_version": str(intent.get("schema_version", RUN_INTENT_SCHEMA_VERSION)),
        "intent": intent,
        "dsse_envelope": sign_dsse(envelope, private_key_path, key_id=key_id),
    }


def build_role_capability_intent(
    *,
    role_id: str,
    capability: str,
    declared_write_scope: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": ROLE_CAPABILITY_SCHEMA_VERSION,
        "role_id": role_id,
        "capability": capability,
        "declared_write_scope": list(declared_write_scope),
    }


def write_signed_run_intent(
    path: str,
    intent: dict[str, Any],
    private_key_path: str,
    *,
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
) -> dict[str, Any]:
    artifact = sign_run_intent(intent, private_key_path, key_id=key_id)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact
