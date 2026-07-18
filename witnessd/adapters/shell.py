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
from pathlib import Path
from typing import Any, Callable, Iterable

from witnessd.changeset import Baseline, capture_snapshot, diff_snapshots, touched_files

_OUTPUT_LIMIT = 4096

TEST_STATUS_NOT_RUN = "not-run"
TEST_STATUS_PASSED = "passed"
TEST_STATUS_FAILED = "failed"
TEST_STATUS_ERROR = "error"


def _scan_argv(command: list[str]) -> None:
    """No-op hook; W4 adapters replace this with prohibited-agent detection."""
    return None


def _snapshot(sandbox: str) -> Baseline:
    return capture_snapshot(sandbox)


def _diff_touched(
    before: Baseline,
    after: Baseline,
    *,
    sandbox: str | None = None,
    evidence_paths: Iterable[str] = (),
) -> list[str]:
    """touched_files diff, optionally excluding adapter-owned evidence paths.

    Defense-in-depth only: the primary defense is the fail-closed evidence-path
    separation check (adapters.base.assert_evidence_path_separated) run before
    any subprocess starts, so evidence paths should never land inside sandbox
    in the first place. This exclusion guards the rarer case where a path
    resolves differently between that check and this after-snapshot.
    """
    touched = touched_files(diff_snapshots(before, after))
    if sandbox is None or not evidence_paths:
        return touched
    root = Path(sandbox).resolve(strict=False)
    excluded: set[str] = set()
    for path in evidence_paths:
        try:
            excluded.add(Path(path).resolve(strict=False).relative_to(root).as_posix())
        except ValueError:
            continue
    return [item for item in touched if item not in excluded]


CommandRunner = Callable[[list[str], str], dict[str, Any]]


def _run_one(
    command: list[str],
    sandbox: str,
    *,
    command_runner: CommandRunner | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    _scan_argv(command)
    if command_runner is not None:
        return command_runner(list(command), sandbox)
    try:
        completed = subprocess.run(
            command,
            cwd=sandbox,
            capture_output=True,
            text=True,
            env=env,
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
    command_runner: CommandRunner | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    before = _snapshot(sandbox)
    env = {**os.environ, **extra_env} if extra_env is not None else None

    command_receipts: list[dict[str, Any]] = []
    for command in commands:
        if argv_scanner is not None:
            argv_scanner(command)
        command_receipts.append(
            _run_one(command, sandbox, command_runner=command_runner, env=env)
        )

    test_output: dict[str, Any] = {"status": TEST_STATUS_NOT_RUN}
    if test_command is not None:
        if argv_scanner is not None:
            argv_scanner(test_command)
        test_receipt = _run_one(
            test_command, sandbox, command_runner=command_runner, env=env
        )
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
