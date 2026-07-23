"""Persistent ORRO roadmap-item task worktree lifecycle."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from witnessd.orro_roadmap import OrroRoadmapError, require_roadmap_item


TASK_DESCRIPTOR_KIND = "orro-task-descriptor"
TASK_DESCRIPTOR_SCHEMA_VERSION = "0.1"
TASK_DESCRIPTOR_NAME = ".orro-task.json"
TASK_OPEN_RECEIPT_NAME = "task-open-receipt.json"

ERR_ORRO_TASK_INVALID = "ERR_ORRO_TASK_INVALID"
ERR_ORRO_TASK_WORKTREE_FAILED = "ERR_ORRO_TASK_WORKTREE_FAILED"
ERR_ORRO_TASK_DESCRIPTOR_FAILED = "ERR_ORRO_TASK_DESCRIPTOR_FAILED"


class OrroTaskError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def task_worktree_path(repo: Path, item_id: str) -> Path:
    return repo / ".orro" / "worktrees" / item_id


def descriptor_path(worktree: Path) -> Path:
    return worktree / TASK_DESCRIPTOR_NAME


def read_task_descriptor(worktree: Path) -> dict[str, Any]:
    path = descriptor_path(worktree)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, "task descriptor is not an object")
    expected = {"kind", "schema_version", "item_id", "worktree", "branch", "base_commit"}
    if set(payload) != expected or payload.get("kind") != TASK_DESCRIPTOR_KIND or payload.get("schema_version") != TASK_DESCRIPTOR_SCHEMA_VERSION:
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, "task descriptor has invalid fields")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in expected - {"kind", "schema_version"}):
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, "task descriptor has invalid values")
    return payload


def _valid_descriptor(worktree: Path, item_id: str) -> dict[str, Any] | None:
    try:
        payload = read_task_descriptor(worktree)
    except OrroTaskError:
        return None
    if payload["item_id"] != item_id:
        return None
    if Path(payload["worktree"]).resolve(strict=False) != worktree.resolve(strict=False):
        return None
    if payload["branch"] != f"orro/{item_id}":
        return None
    return payload


def scan_task_worktrees(repo: Path) -> dict[str, dict[str, Any]]:
    """Discover valid task descriptors; there is no second task registry."""

    root = repo.resolve(strict=False) / ".orro" / "worktrees"
    result: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.iterdir(), key=str):
        if not path.is_dir():
            continue
        try:
            descriptor = read_task_descriptor(path)
        except OrroTaskError:
            continue
        item_id = descriptor.get("item_id")
        if isinstance(item_id, str) and _valid_descriptor(path, item_id) is not None:
            result[item_id] = {"path": path.resolve(strict=False), "descriptor": descriptor}
    return result


def discover_task_workspaces(repo: Path) -> list[dict[str, Any]]:
    root = repo.resolve(strict=False) / ".orro" / "worktrees"
    result: list[dict[str, Any]] = []
    if not root.is_dir():
        return result
    for path in sorted(root.iterdir(), key=str):
        if not path.is_dir():
            continue
        descriptor: dict[str, Any] | None
        try:
            descriptor = read_task_descriptor(path)
        except OrroTaskError:
            descriptor = None
        item_id = descriptor.get("item_id") if descriptor else path.name
        valid = isinstance(item_id, str) and _valid_descriptor(path, item_id) is not None
        result.append({"path": path.resolve(strict=False), "item_id": item_id, "descriptor": descriptor, "valid": valid})
    return result


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def _git_value(repo: Path, *args: str) -> str:
    completed = _git(repo, *args)
    if completed.returncode != 0:
        raise OrroTaskError(
            ERR_ORRO_TASK_WORKTREE_FAILED,
            completed.stderr.strip() or completed.stdout.strip() or "git command failed",
        )
    return completed.stdout.strip()


def _seal_descriptor(worktree: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = descriptor_path(worktree)
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return read_task_descriptor(worktree)
    except (OSError, OrroTaskError) as exc:
        if isinstance(exc, OrroTaskError):
            raise
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, str(exc)) from exc


def _run_open_hook(*, repo: Path, worktree: Path, item_id: str, branch: str, command: str) -> int:
    rendered = command.replace("{path}", str(worktree)).replace("{item_id}", item_id).replace("{branch}", branch)
    try:
        completed = subprocess.run(shlex.split(rendered), cwd=repo, text=True, capture_output=True, check=False)
        exit_code = completed.returncode
    except (OSError, ValueError) as exc:
        exit_code = 127
        rendered = f"{rendered} ({exc})"
    receipt = {"command": rendered, "exit_code": exit_code}
    receipt_path = worktree / TASK_OPEN_RECEIPT_NAME
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrroTaskError(ERR_ORRO_TASK_DESCRIPTOR_FAILED, f"open receipt was not readable: {exc}") from exc
    return exit_code


def begin_task(*, repo: Path, item_id: str, base: str | None = None, no_open: bool = False) -> dict[str, Any]:
    repo = repo.resolve(strict=False)
    require_roadmap_item(repo, item_id)
    worktree = task_worktree_path(repo, item_id).resolve(strict=False)
    branch = f"orro/{item_id}"
    branch_exists = _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    state: str
    if worktree.is_dir():
        state = "resumed"
    else:
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if branch_exists:
            completed = _git(repo, "worktree", "add", str(worktree), branch)
            state = "attached"
        else:
            resolved_base = _git_value(repo, "rev-parse", base or "HEAD")
            completed = _git(repo, "worktree", "add", "-b", branch, str(worktree), resolved_base)
            state = "created"
        if completed.returncode != 0:
            raise OrroTaskError(ERR_ORRO_TASK_WORKTREE_FAILED, completed.stderr.strip() or completed.stdout.strip() or "git worktree add failed")

    existing = _valid_descriptor(worktree, item_id)
    if existing is not None:
        base_commit = existing["base_commit"]
    else:
        base_commit = _git_value(worktree, "rev-parse", "HEAD")
    descriptor = _seal_descriptor(
        worktree,
        {
            "kind": TASK_DESCRIPTOR_KIND,
            "schema_version": TASK_DESCRIPTOR_SCHEMA_VERSION,
            "item_id": item_id,
            "worktree": str(worktree),
            "branch": branch,
            "base_commit": base_commit,
        },
    )
    result: dict[str, Any] = {
        "kind": "orro-task-begin",
        "schema_version": "0.1",
        "item_id": item_id,
        "state": state,
        "worktree": str(worktree),
        "branch": branch,
        "base_commit": descriptor["base_commit"],
        "descriptor": str(descriptor_path(worktree)),
        "boundary": "The worktree, its branch, and its commits are workspace state, not proof; task begin output is setup metadata — not proof, not verifier truth, not approval, not assurance. Merge approval and merge execution stay human; ORRO never merges. Panes/agent/session state belong to the workspace runtime, never sealed into evidence.",
    }
    command = os.environ.get("ORRO_TASK_OPEN_COMMAND")
    if command and not no_open:
        exit_code = _run_open_hook(repo=repo, worktree=worktree, item_id=item_id, branch=branch, command=command)
        result["open_hook_exit_code"] = exit_code
        result["open_hook_command"] = command
    else:
        result["message"] = "open hook not configured (set ORRO_TASK_OPEN_COMMAND)" if not command else "open hook skipped (--no-open)"
    return result
