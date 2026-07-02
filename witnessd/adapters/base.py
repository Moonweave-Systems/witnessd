"""Adapter normalization contract for W4 runner lanes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from depone.agent_fabric.paired_run import VALID_RUNNERS

from witnessd.receipt import build_runner_receipt

RUNNER_KIND_BY_ADAPTER = {
    "codex": "codex-cli",
    "claude": "manual",
    "opencode": "manual",
}


class RunnerKindError(ValueError):
    pass


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


def _self_test() -> None:
    assert set(RUNNER_KIND_BY_ADAPTER.values()) <= VALID_RUNNERS
    try:
        assert_runner_kind_valid("not-a-runner")
    except RunnerKindError:
        pass
    else:
        raise AssertionError("unknown runner_kind must fail closed")
