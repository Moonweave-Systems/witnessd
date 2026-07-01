"""Shell adapter — run a lane's commands and emit command_receipts (E3).

Each command in a lane is executed inside the runner sandbox
(`subprocess.run(cwd=sandbox, capture_output=True)`); every invocation yields a
receipt carrying its `command` (list[str]) and int `exit_code` (plus truncated
stdout/stderr). `touched_files` is derived from a before/after snapshot diff of
the sandbox tree. `test_output.status` is drawn from the Depone-valid enum
{not-run, passed, failed, error}: it stays `not-run` unless a dedicated
`test_command` is supplied, in which case its exit code classifies the run.

Prohibited-agent argv scanning (codex/claude/opencode) is a W4 adapter concern;
this lane leaves a no-op scan hook so the wiring point already exists.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Callable

_OUTPUT_LIMIT = 4096

TEST_STATUS_NOT_RUN = "not-run"
TEST_STATUS_PASSED = "passed"
TEST_STATUS_FAILED = "failed"
TEST_STATUS_ERROR = "error"


def _scan_argv(command: list[str]) -> None:
    """No-op hook; W4 adapters replace this with prohibited-agent detection."""
    return None


def _snapshot(sandbox: str) -> dict[str, tuple[int, float]]:
    snapshot: dict[str, tuple[int, float]] = {}
    for root, _dirs, files in os.walk(sandbox):
        for name in files:
            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, sandbox)
            try:
                stat = os.stat(abs_path)
            except OSError:
                continue
            snapshot[rel_path] = (stat.st_size, stat.st_mtime)
    return snapshot


def _diff_touched(
    before: dict[str, tuple[int, float]], after: dict[str, tuple[int, float]]
) -> list[str]:
    touched = [rel for rel, meta in after.items() if before.get(rel) != meta]
    return sorted(touched)


def _run_one(command: list[str], sandbox: str) -> dict[str, Any]:
    _scan_argv(command)
    try:
        completed = subprocess.run(
            command,
            cwd=sandbox,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {
            "command": list(command),
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc)[:_OUTPUT_LIMIT],
        }
    return {
        "command": list(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout[:_OUTPUT_LIMIT],
        "stderr": completed.stderr[:_OUTPUT_LIMIT],
    }


def run_shell_lane(
    sandbox: str,
    commands: list[list[str]],
    *,
    test_command: list[str] | None = None,
    argv_scanner: Callable[[list[str]], None] | None = None,
) -> dict[str, Any]:
    before = _snapshot(sandbox)

    command_receipts: list[dict[str, Any]] = []
    for command in commands:
        if argv_scanner is not None:
            argv_scanner(command)
        command_receipts.append(_run_one(command, sandbox))

    test_output: dict[str, Any] = {"status": TEST_STATUS_NOT_RUN}
    if test_command is not None:
        if argv_scanner is not None:
            argv_scanner(test_command)
        test_receipt = _run_one(test_command, sandbox)
        command_receipts.append(test_receipt)
        if test_receipt["exit_code"] == 127:
            test_output = {"status": TEST_STATUS_ERROR}
        elif test_receipt["exit_code"] == 0:
            test_output = {"status": TEST_STATUS_PASSED}
        else:
            test_output = {"status": TEST_STATUS_FAILED}

    after = _snapshot(sandbox)
    touched_files = _diff_touched(before, after)

    return {
        "command_receipts": command_receipts,
        "touched_files": touched_files,
        "test_output": test_output,
    }
