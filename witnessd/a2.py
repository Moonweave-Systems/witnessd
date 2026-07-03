"""W12 real A2 observer-runner wiring.

This module is intentionally small: it runs the observer side in the current
process, executes the shell lane through an injected runner command runner, and
then feeds the existing uid-boundary verifier facts into the normal emitter.
"""

from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path
from typing import Any

from witnessd.adapters.shell import CommandRunner, run_shell_lane
from witnessd.emitter import emit_supervised_lane
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.isolation import UID_OBSERVER_LAUNCHED_ISOLATION_MODEL
from witnessd.observer import assert_separated

_OUTPUT_LIMIT = 4096


def uid_for_user(user: str) -> int:
    return int(pwd.getpwnam(user).pw_uid)


def sudo_runner_for_user(user: str) -> CommandRunner:
    """Return a command runner that executes lane commands as ``user`` via sudo."""

    def _runner(command: list[str], sandbox: str) -> dict[str, Any]:
        actual = ["sudo", "-n", "-u", user, "--", *command]
        try:
            completed = subprocess.run(
                actual,
                cwd=sandbox,
                capture_output=True,
                text=True,
                check=False,
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

    return _runner


def run_observer_launched_shell_lane(
    *,
    sandbox: str,
    commands: list[list[str]],
    evidence_dir: str,
    private_key_path: str,
    public_key_path: str,
    observer_dir: str,
    runner_user: str,
    allowed_touched_files: list[str],
    task_id: str = "w12-real-a2",
    test_command: list[str] | None = None,
    runner_uid: int | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Run one shell lane from an observer process with a uid-separated runner.

    The caller is expected to run this function under the dedicated observer uid.
    The runner command path defaults to passwordless ``sudo -n -u runner_user``.
    Tests pass a fake command runner and mocked uid facts, so no privilege is
    needed for Phase A.
    """

    sandbox_path = os.path.realpath(sandbox)
    evidence_path = Path(evidence_dir).resolve()
    manifest_path = evidence_path / "capture-manifest.json"
    assert_separated(runner_sandbox=sandbox_path, out_path=str(manifest_path))

    resolved_runner_uid = (
        runner_uid if runner_uid is not None else uid_for_user(runner_user)
    )
    runner = command_runner or sudo_runner_for_user(runner_user)
    lane_result = run_shell_lane(
        sandbox=sandbox_path,
        commands=commands,
        test_command=test_command,
        command_runner=runner,
    )
    fixture = build_reference_adapter_fixture(build_shell_invocation(task_id))
    return emit_supervised_lane(
        lane_result,
        str(evidence_path),
        private_key_path,
        fixture=fixture,
        allowed_touched_files=allowed_touched_files,
        public_key_path=public_key_path,
        observer_dir=observer_dir,
        runner_uid=resolved_runner_uid,
        task_id=task_id,
        invocation=commands[0] if commands else ["sh", "-c", "true"],
        runner_sandbox=sandbox_path,
        isolation_model=UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
        observer_launched=True,
    )
