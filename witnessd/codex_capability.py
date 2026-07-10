"""Codex local capability receipt builder for witnessd runtime.

This is a stdlib emit-side copy of the Depone capability receipt contract. It
does not launch a coding task or depend on the Depone package at runtime.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

CODEX_LOCAL_CAPABILITY_KIND = "depone-codex-local-capability"
CODEX_LOCAL_CAPABILITY_SCHEMA_VERSION = "0.1"
DEFAULT_CODEX_ROLE_ID = "worker"
ALLOWED_SANDBOX_MODES = frozenset({"read-only", "workspace-write"})
ALLOWED_APPROVAL_POLICIES = frozenset(
    {"never", "on-request", "on-failure", "untrusted"}
)

AGENT_CONTRACT_FACTS = {
    "agent_contract_id": "depone-agent-operating-contract.v0.1",
    "agent_contract_hash": "7f8609766f1aad439050f876ea2c5261cde3715070b27fd4fbfefd81c43d71e8",
    "role_id": "worker",
    "role_registry_path": "packaging/dwm-roles.json",
    "role_registry_sha256": "8a5ea14ff29d9f00c7b253364912f95c946f6c53616e16804ab96d5bbbdb36fc",
}


def build_codex_local_capability(
    *,
    repo: Path,
    codex_binary: str = "codex",
    sandbox_mode: str = "workspace-write",
    approval_policy: str = "on-request",
    version_timeout_seconds: float = 10,
    instruction_files: list[Path] | None = None,
    role_id: str = DEFAULT_CODEX_ROLE_ID,
) -> dict[str, object]:
    """Build a blocked/pass capability receipt without launching Codex."""

    resolved_repo = repo.resolve()
    blocked_reasons: list[str] = []
    binary_path = shutil.which(codex_binary)
    if binary_path is None:
        blocked_reasons.append("codex binary not found")
        version_probe = _version_probe_not_run()
    else:
        version_probe = _probe_codex_version(binary_path, version_timeout_seconds)
        blocked_reasons.extend(_version_probe_blocked_reasons(version_probe))
    if sandbox_mode not in ALLOWED_SANDBOX_MODES:
        blocked_reasons.append("unsupported sandbox mode")
    if approval_policy not in ALLOWED_APPROVAL_POLICIES:
        blocked_reasons.append("unsupported approval policy")
    git_facts = _git_facts(resolved_repo)
    if git_facts.get("is_git_worktree") is not True:
        blocked_reasons.append("repo is not a git worktree")
    probe_errors = git_facts.get("probe_errors")
    if isinstance(probe_errors, list):
        for probe_error in probe_errors:
            if isinstance(probe_error, str):
                blocked_reasons.append(probe_error)
    if git_facts.get("dirty") is True:
        blocked_reasons.append("repo working tree is dirty")
    contract = _agent_contract_facts(role_id)
    instructions = _instruction_facts(resolved_repo, instruction_files or [])
    if any("blocked_reason" in fact for fact in instructions):
        blocked_reasons.append("instruction file path outside repo boundary")
    return {
        "kind": CODEX_LOCAL_CAPABILITY_KIND,
        "schema_version": CODEX_LOCAL_CAPABILITY_SCHEMA_VERSION,
        "decision": "blocked" if blocked_reasons else "pass",
        "blocked_reasons": blocked_reasons,
        "adapter": {
            "id": "codex-local",
            "codex_binary": codex_binary,
            "binary_path": binary_path,
            "version": version_probe.get("sanitized_version_text"),
        },
        "readiness": {
            "version_probe": version_probe,
        },
        "repo": git_facts,
        "requested_runtime": {
            "sandbox_mode": sandbox_mode,
            "approval_policy": approval_policy,
        },
        "instruction_files": instructions,
        "agent_contract_hash": contract["agent_contract_hash"],
        "agent_contract": contract,
        "boundary": {
            "launches_live_model": False,
            "executes_coding_task": False,
            "captures_capability_only": True,
            "raises_assurance": False,
        },
    }


def validate_codex_local_capability(receipt: dict[str, object]) -> list[str]:
    """Return validation errors for a Codex local capability receipt."""

    errors: list[str] = []
    if receipt.get("kind") != CODEX_LOCAL_CAPABILITY_KIND:
        errors.append("kind must be depone-codex-local-capability")
    if receipt.get("schema_version") != CODEX_LOCAL_CAPABILITY_SCHEMA_VERSION:
        errors.append("schema_version must be 0.1")
    if receipt.get("decision") not in {"pass", "blocked"}:
        errors.append("decision must be pass or blocked")
    if receipt.get("decision") == "blocked" and not receipt.get("blocked_reasons"):
        errors.append("blocked decision requires blocked_reasons")
    readiness = receipt.get("readiness")
    if not isinstance(readiness, dict):
        errors.append("readiness must be an object")
    else:
        _validate_version_probe(readiness.get("version_probe"), errors)
    boundary = receipt.get("boundary")
    if not isinstance(boundary, dict):
        errors.append("boundary must be an object")
    else:
        for key in ("launches_live_model", "executes_coding_task", "raises_assurance"):
            if boundary.get(key) is not False:
                errors.append(f"boundary.{key} must be false")
        if boundary.get("captures_capability_only") is not True:
            errors.append("boundary.captures_capability_only must be true")
    agent_contract = receipt.get("agent_contract")
    if not isinstance(agent_contract, dict):
        errors.append("agent_contract must be an object")
    elif receipt.get("agent_contract_hash") != agent_contract.get("agent_contract_hash"):
        errors.append("agent_contract_hash mismatch")
    return errors


def _agent_contract_facts(role_id: str) -> dict[str, object]:
    if role_id != DEFAULT_CODEX_ROLE_ID:
        raise ValueError("agent contract facts invalid: ERR_AGENT_CONTRACT_V22_ROLE_ID_MISMATCH")
    return dict(AGENT_CONTRACT_FACTS)


def _version_probe_not_run() -> dict[str, object]:
    return {
        "executed": False,
        "argv": ["codex", "--version"],
        "exit_code": None,
        "timed_out": False,
        "stdout_present": False,
        "stderr_present": False,
        "sanitized_version_text": None,
        "unexpected_output": False,
        "error": "binary_not_found",
    }


def _probe_codex_version(binary_path: str, timeout_seconds: float) -> dict[str, object]:
    probe: dict[str, object] = {
        "executed": True,
        "argv": ["codex", "--version"],
        "exit_code": None,
        "timed_out": False,
        "stdout_present": False,
        "stderr_present": False,
        "sanitized_version_text": None,
        "unexpected_output": False,
        "error": None,
    }
    try:
        completed = subprocess.run(
            [binary_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        probe["timed_out"] = True
        probe["error"] = "timeout"
        return probe
    except OSError:
        probe["error"] = "exec_error"
        return probe

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    probe["exit_code"] = completed.returncode
    probe["stdout_present"] = bool(stdout.strip())
    probe["stderr_present"] = bool(stderr.strip())
    if completed.returncode != 0:
        probe["error"] = "nonzero_exit"
        return probe

    version_text = _sanitize_codex_version_text(stdout.strip() or stderr.strip())
    if version_text is None:
        probe["unexpected_output"] = True
        probe["error"] = "unexpected_output"
        return probe

    probe["sanitized_version_text"] = version_text
    return probe


def _sanitize_codex_version_text(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = lines[0]
    if len(line) > 120 or "codex" not in line.lower():
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in line):
        return None
    return line


def _version_probe_blocked_reasons(probe: dict[str, object]) -> list[str]:
    if probe.get("timed_out") is True:
        return ["codex version probe timed out"]
    error = probe.get("error")
    if error == "nonzero_exit":
        return ["codex version probe failed"]
    if error == "unexpected_output":
        return ["codex version probe returned unexpected output"]
    if error == "exec_error":
        return ["codex version probe could not execute"]
    return []


def _validate_version_probe(probe: Any, errors: list[str]) -> None:
    if not isinstance(probe, dict):
        errors.append("readiness.version_probe must be an object")
        return
    if not isinstance(probe.get("executed"), bool):
        errors.append("readiness.version_probe.executed must be boolean")
    if not isinstance(probe.get("timed_out"), bool):
        errors.append("readiness.version_probe.timed_out must be boolean")
    if not isinstance(probe.get("stdout_present"), bool):
        errors.append("readiness.version_probe.stdout_present must be boolean")
    if not isinstance(probe.get("stderr_present"), bool):
        errors.append("readiness.version_probe.stderr_present must be boolean")
    if not isinstance(probe.get("unexpected_output"), bool):
        errors.append("readiness.version_probe.unexpected_output must be boolean")
    argv = probe.get("argv")
    if argv != ["codex", "--version"]:
        errors.append("readiness.version_probe.argv must be sanitized codex --version")
    exit_code = probe.get("exit_code")
    if exit_code is not None and not isinstance(exit_code, int):
        errors.append("readiness.version_probe.exit_code must be int or null")
    version_text = probe.get("sanitized_version_text")
    if version_text is not None and _sanitize_codex_version_text(str(version_text)) != version_text:
        errors.append("readiness.version_probe.sanitized_version_text is invalid")


def _git_facts(repo: Path) -> dict[str, object]:
    root = _git_probe(repo, ["rev-parse", "--show-toplevel"], "git root unknown")
    head = _git_probe(repo, ["rev-parse", "HEAD"], "git HEAD unknown")
    branch = _git_probe(repo, ["branch", "--show-current"], "git branch unknown")
    status = _git_probe(repo, ["status", "--porcelain"], "git status unknown")
    probe_errors = [
        str(probe["error"])
        for probe in (root, head, branch, status)
        if probe["error"] is not None
    ]
    return {
        "path": repo.as_posix(),
        "is_git_worktree": root["known"],
        "root": root["value"],
        "head": head["value"],
        "branch": branch["value"],
        "dirty": None if not status["known"] else bool(status["value"]),
        "probe_errors": probe_errors,
        "error": "; ".join(probe_errors) if probe_errors else None,
    }


def _git_probe(repo: Path, args: list[str], error_label: str) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["git", "-C", repo.as_posix(), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"known": False, "value": None, "error": error_label}
    if completed.returncode != 0:
        return {"known": False, "value": None, "error": error_label}
    return {"known": True, "value": completed.stdout.strip(), "error": None}


def _git(repo: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", repo.as_posix(), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _instruction_facts(repo: Path, instruction_files: list[Path]) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for path in instruction_files:
        if path.is_absolute() or ".." in path.parts:
            facts.append(
                {
                    "path": path.as_posix(),
                    "present": False,
                    "sha256": None,
                    "blocked_reason": "instruction file must be repo-relative",
                }
            )
            continue
        resolved = path if path.is_absolute() else repo / path
        try:
            resolved.lstat()
        except OSError:
            facts.append({"path": path.as_posix(), "present": False, "sha256": None})
            continue
        if resolved.is_symlink():
            facts.append(
                {
                    "path": path.as_posix(),
                    "present": False,
                    "sha256": None,
                    "blocked_reason": "instruction file must not be a symlink",
                }
            )
            continue
        try:
            resolved.resolve(strict=True).relative_to(repo)
        except (OSError, ValueError):
            facts.append(
                {
                    "path": path.as_posix(),
                    "present": False,
                    "sha256": None,
                    "blocked_reason": "instruction file target escapes repo boundary",
                }
            )
            continue
        if not resolved.is_file():
            facts.append({"path": path.as_posix(), "present": False, "sha256": None})
            continue
        facts.append(
            {
                "path": path.as_posix(),
                "present": True,
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
            }
        )
    return facts
