"""Adapter normalization contract for W4 runner lanes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from depone.agent_fabric.paired_run import VALID_RUNNERS

from witnessd.adapters.shell import TEST_STATUS_NOT_RUN, _diff_touched, _snapshot
from witnessd.receipt import build_runner_receipt

RUNNER_KIND_BY_ADAPTER = {
    "codex": "codex-cli",
    "claude": "manual",
    "opencode": "manual",
}


class RunnerKindError(ValueError):
    pass


class AdapterExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def assert_runner_kind_valid(runner_kind: str) -> None:
    if runner_kind not in VALID_RUNNERS:
        raise RunnerKindError(
            f"runner_kind must be one of {sorted(VALID_RUNNERS)}"
        )


@dataclass(frozen=True)
class AdapterResult:
    adapter: str
    runner_kind: str
    invocation: list[str]
    exit_code: int
    transcript_path: str
    command_receipts: list[dict[str, Any]]
    touched_files: list[str]
    test_output: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.invocation:
            raise ValueError("ERR_ADAPTER_INVOCATION_REQUIRED")
        assert_runner_kind_valid(self.runner_kind)
        expected = RUNNER_KIND_BY_ADAPTER.get(self.adapter)
        if expected is None:
            raise RunnerKindError(f"unknown adapter: {self.adapter}")
        if self.runner_kind != expected:
            raise RunnerKindError(
                f"runner_kind for {self.adapter} must be {expected}"
            )

    def to_runner_receipt(
        self,
        *,
        arm: str,
        task_id: str,
        worktree: str,
        started_at: str,
        ended_at: str,
        human_intervened: bool = False,
    ) -> dict[str, Any]:
        return build_runner_receipt(
            task_id=task_id,
            worktree=worktree,
            invocation=self.invocation,
            transcript_path=self.transcript_path,
            exit_code=self.exit_code,
            touched_files=self.touched_files,
            started_at=started_at,
            ended_at=ended_at,
            arm=arm,
            runner_kind=self.runner_kind,
            human_intervened=human_intervened,
        )


def _resolve_executable(binary: str, *, unavailable_code: str) -> str:
    if os.path.sep in binary or (os.path.altsep is not None and os.path.altsep in binary):
        path = Path(binary)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        raise AdapterExecutionError(
            unavailable_code, f"binary is not executable: {binary}"
        )

    resolved = shutil.which(binary)
    if resolved is None:
        raise AdapterExecutionError(unavailable_code, f"binary not found: {binary}")
    return resolved


def _write_command_log(
    log_path: str,
    *,
    command: list[str],
    cwd: str,
    stdout: str,
    stderr: str,
    exit_code: int,
) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "command": command,
                "cwd": cwd,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _timeout_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _run_cli_lane(
    *,
    adapter: str,
    runner_kind: str,
    sandbox: str,
    invocation: list[str],
    transcript_path: str,
    log_path: str | None,
    timeout_seconds: int,
) -> AdapterResult:
    repo = str(Path(sandbox).resolve(strict=False))
    transcript = str(Path(transcript_path).resolve(strict=False))
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            invocation,
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
    except OSError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)

    if log_path is not None:
        _write_command_log(
            log_path,
            command=invocation,
            cwd=repo,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    if not Path(transcript).exists():
        Path(transcript).write_text((stdout or "") + (stderr or ""), encoding="utf-8")

    after = _snapshot(repo)
    return AdapterResult(
        adapter=adapter,
        runner_kind=runner_kind,
        invocation=invocation,
        exit_code=exit_code,
        transcript_path=transcript,
        command_receipts=[
            {
                "command": invocation,
                "cwd": repo,
                "exit_code": exit_code,
                "stdout": stdout[:4096],
                "stderr": stderr[:4096],
            }
        ],
        touched_files=_diff_touched(before, after),
        test_output={"status": TEST_STATUS_NOT_RUN},
    )


def _self_test() -> None:
    assert set(RUNNER_KIND_BY_ADAPTER.values()) <= VALID_RUNNERS
    try:
        assert_runner_kind_valid("not-a-runner")
    except RunnerKindError:
        pass
    else:
        raise AssertionError("unknown runner_kind must fail closed")
