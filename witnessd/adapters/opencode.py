"""OpenCode adapter for W4 runner lanes."""

from __future__ import annotations

from witnessd.adapters.base import (
    AdapterExecutionError,
    AdapterResult,
    _resolve_executable,
    _run_cli_lane,
)


class OpenCodeAdapterError(AdapterExecutionError):
    pass


def _opencode_binary(path: str) -> str:
    try:
        return _resolve_executable(path, unavailable_code="ERR_OPENCODE_UNAVAILABLE")
    except AdapterExecutionError as exc:
        raise OpenCodeAdapterError(exc.code, exc.message) from exc


def run_opencode_lane(
    *,
    sandbox: str,
    prompt: str,
    opencode_binary: str = "opencode",
    transcript_path: str,
    log_path: str | None = None,
    timeout_seconds: int = 120,
) -> AdapterResult:
    if not prompt.strip():
        raise OpenCodeAdapterError(
            "ERR_OPENCODE_PROMPT_MISSING", "opencode prompt must not be empty"
        )
    invocation = [_opencode_binary(opencode_binary), "run", prompt]
    return _run_cli_lane(
        adapter="opencode",
        runner_kind="manual",
        sandbox=sandbox,
        invocation=invocation,
        transcript_path=transcript_path,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
        error_cls=OpenCodeAdapterError,
    )
