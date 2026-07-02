"""Crash-safe durable session state for run_id resume."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SessionResumeError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def _run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        final_path = run_dir / "session.json"
        tmp_path = run_dir / f".session.{os.getpid()}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, final_path)
            dir_fd = os.open(run_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def resume(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "session.json"
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            raise SessionResumeError(f"ERR_SESSION_RESUME_BLOCKED: {run_id}") from exc
        if not isinstance(state, dict):
            raise SessionResumeError(f"ERR_SESSION_RESUME_BLOCKED: {run_id}")
        return state


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(tmp)
        store.save("R", {"tool_call_cursor": 1})
        assert store.resume("R")["tool_call_cursor"] == 1
