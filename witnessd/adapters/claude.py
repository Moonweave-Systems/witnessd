"""Claude Code adapter for W4 runner lanes."""

from __future__ import annotations

from witnessd.adapters.base import (
    AdapterExecutionError,
    AdapterResult,
    _resolve_executable,
    _run_cli_lane,
)


class ClaudeAdapterError(AdapterExecutionError):
    pass


def _claude_binary(path: str) -> str:
    try:
        return _resolve_executable(path, unavailable_code="ERR_CLAUDE_UNAVAILABLE")
    except AdapterExecutionError as exc:
        raise ClaudeAdapterError(exc.code, exc.message) from exc


def run_claude_lane(
    *,
    sandbox: str,
    prompt: str,
    claude_binary: str = "claude",
    transcript_path: str,
    log_path: str | None = None,
    timeout_seconds: int = 120,
) -> AdapterResult:
    if not prompt.strip():
        raise ClaudeAdapterError(
            "ERR_CLAUDE_PROMPT_MISSING", "claude prompt must not be empty"
        )
    invocation = [_claude_binary(claude_binary), "-p", prompt]
    return _run_cli_lane(
        adapter="claude",
        runner_kind="manual",
        sandbox=sandbox,
        invocation=invocation,
        transcript_path=transcript_path,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
    )
