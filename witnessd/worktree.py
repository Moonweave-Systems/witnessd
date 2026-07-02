"""Git worktree helpers for W3 lane isolation."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Any

WORKTREE_LANE_RECEIPT_KIND = "depone-worktree-lane-receipt"
WORKTREE_LANE_RECEIPT_SCHEMA_VERSION = "0.1"


class WorktreeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _lane_slug(lane_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", lane_id).strip("-")
    return slug or "lane"


def create_lane_worktree(
    *,
    repo_root: str,
    lane_id: str,
    base_commit: str,
    worktrees_dir: str,
) -> str:
    root = Path(repo_root).resolve()
    lane_slug = _lane_slug(lane_id)
    target = (Path(worktrees_dir) / lane_slug).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    branch = _worktree_branch(root, lane_slug, target)
    command = [
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(target),
        base_commit,
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise WorktreeError(
            "ERR_WORKTREE_CREATE_FAILED",
            completed.stderr.strip() or completed.stdout.strip(),
        )
    return os.path.abspath(target)


def _worktree_branch(root: Path, lane_slug: str, target: Path) -> str:
    base_branch = f"witnessd/{lane_slug}"
    if not _branch_exists(root, base_branch):
        return base_branch
    suffix = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:12]
    branch = f"{base_branch}-{suffix}"
    counter = 2
    while _branch_exists(root, branch):
        branch = f"{base_branch}-{suffix}-{counter}"
        counter += 1
    return branch


def _branch_exists(root: Path, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def build_worktree_lane_receipt(
    *,
    worktree: str,
    base_commit: str,
    evidence_dir: str,
    commands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not str(base_commit).strip():
        raise WorktreeError(
            "ERR_WORKTREE_RECEIPT_BASE_COMMIT_REQUIRED",
            "base_commit must be a non-empty git revision",
        )
    evidence_dir_text = _normalize_relative_path(evidence_dir, "evidence_dir")
    repo_root = _repo_root(Path(worktree))
    _verify_commit(repo_root, base_commit)
    head_commit = _git(repo_root, ["rev-parse", "HEAD"])
    branch = _git(repo_root, ["branch", "--show-current"])
    if not branch:
        branch = _git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    changed_files = _git_lines(
        repo_root, ["diff", "--name-only", base_commit, "HEAD", "--"]
    )
    dirty_files = _dirty_files(repo_root)
    command_receipts = commands or []
    if not isinstance(command_receipts, list) or not all(
        isinstance(item, dict) for item in command_receipts
    ):
        raise WorktreeError(
            "ERR_WORKTREE_RECEIPT_COMMAND_RECEIPTS_INVALID",
            "command receipts must be JSON objects",
        )
    return {
        "kind": WORKTREE_LANE_RECEIPT_KIND,
        "schema_version": WORKTREE_LANE_RECEIPT_SCHEMA_VERSION,
        "worktree": str(repo_root),
        "branch": branch,
        "base_commit": base_commit,
        "head_commit": head_commit,
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files,
        "changed_files": changed_files,
        "evidence_dir": evidence_dir_text,
        "command_receipts": command_receipts,
        "boundary": {
            "executes_commands": False,
            "launches_agents": False,
            "mutates_worktree": False,
            "git_read_only": True,
        },
    }


def _repo_root(worktree: Path) -> Path:
    if not worktree.is_dir():
        raise WorktreeError(
            "ERR_WORKTREE_RECEIPT_REPO_MISSING",
            "worktree must be an existing git worktree directory",
        )
    try:
        root = _git(worktree, ["rev-parse", "--show-toplevel"])
    except WorktreeError as exc:
        raise WorktreeError(
            "ERR_WORKTREE_RECEIPT_REPO_MISSING",
            "worktree must be an existing git worktree directory",
        ) from exc
    return Path(root)


def _verify_commit(repo_root: Path, revision: str) -> None:
    _git(repo_root, ["cat-file", "-e", f"{revision}^{{commit}}"])


def _dirty_files(repo_root: Path) -> list[str]:
    files: set[str] = set()
    for line in _git_lines(repo_root, ["status", "--porcelain=v1"]):
        if len(line) < 4:
            continue
        path_text = line[3:]
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        files.add(_normalize_relative_path(path_text, "dirty file"))
    return sorted(files)


def _git_lines(cwd: Path, args: list[str]) -> list[str]:
    output = _git(cwd, args)
    return sorted(line for line in output.splitlines() if line)


def _git(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "git command failed"
        )
        raise WorktreeError("ERR_WORKTREE_RECEIPT_GIT_FAILED", message)
    return completed.stdout.strip()


def _normalize_relative_path(path: str, label: str) -> str:
    text = str(path).replace("\\", "/")
    normalized = posixpath.normpath(text)
    parts = normalized.split("/")
    if not text or normalized.startswith("/") or ".." in parts:
        raise WorktreeError(
            "ERR_WORKTREE_RECEIPT_PATH_INVALID",
            f"{label} must be a non-empty relative path",
        )
    return normalized


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "w3"], cwd=repo, check=True)
        (repo / "seed.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        path = create_lane_worktree(
            repo_root=str(repo),
            lane_id="L1",
            base_commit=base,
            worktrees_dir=str(Path(tmp) / "worktrees"),
        )
        assert Path(path).is_dir()
        receipt = build_worktree_lane_receipt(
            worktree=path,
            base_commit=base,
            evidence_dir="lane-evidence",
        )
        assert receipt["kind"] == WORKTREE_LANE_RECEIPT_KIND
