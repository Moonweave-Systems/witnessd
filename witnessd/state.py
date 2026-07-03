"""W4 state namespace isolation and contention checks."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class StateContentionError(RuntimeError):
    def __init__(self, message: str = "state namespace is already locked") -> None:
        super().__init__(f"ERR_WITNESSD_STATE_CONTENTION: {message}")
        self.code = "ERR_WITNESSD_STATE_CONTENTION"


class StateNamespace:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve(strict=False)
        self.state_dir = self.root / ".witnessd"
        self.runlog_path = str(self.state_dir / "runlog.jsonl")
        self.session_dir = str(self.state_dir / "sessions")
        self.worktree_root = str(self.state_dir / "worktrees")
        self._lock_handle = None

    def __enter__(self) -> "StateNamespace":
        self.state_dir.mkdir(parents=True, exist_ok=True)
        Path(self.session_dir).mkdir(parents=True, exist_ok=True)
        Path(self.worktree_root).mkdir(parents=True, exist_ok=True)
        lock_path = self.state_dir / "lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise StateContentionError(str(lock_path)) from exc
        self._lock_handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def codex_env(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(base_env or os.environ)
        codex_home = self.state_dir / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _paths_overlap(left: str, right: str) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    try:
        common = os.path.commonpath([left_norm, right_norm])
    except ValueError:
        return False
    return common in {left_norm, right_norm}


def detect_state_contention(
    *,
    witnessd_worktree: str,
    external_active_worktrees: list[str],
) -> list[str]:
    errors: list[str] = []
    for external in external_active_worktrees:
        if _paths_overlap(witnessd_worktree, external):
            errors.append(f"ERR_WITNESSD_STATE_CONTENTION: {external}")
    return errors


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        with StateNamespace(root) as namespace:
            if not os.path.realpath(namespace.runlog_path).startswith(
                os.path.realpath(str(Path(root) / ".witnessd"))
            ):
                raise AssertionError("runlog must stay inside .witnessd namespace")
            try:
                StateNamespace(root).__enter__()
            except StateContentionError:
                pass
            else:
                raise AssertionError("state lock must be exclusive")
        errors = detect_state_contention(
            witnessd_worktree=str(Path(root) / "wt"),
            external_active_worktrees=[str(Path(root) / "wt" / "child")],
        )
        if not errors:
            raise AssertionError("overlapping active worktree must be reported")
