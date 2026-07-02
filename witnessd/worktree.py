"""Git worktree helpers for W3 lane isolation."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


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
    branch = f"witnessd/{lane_slug}"
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
