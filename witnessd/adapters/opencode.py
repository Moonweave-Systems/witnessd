"""OpenCode adapter for W4 runner lanes.

EXPERIMENTAL, not independently confirmed against a real CLI: outside the
codex/claude/agy set witnessd otherwise checks against real CLIs. Checked
live against opencode 1.17.10: `opencode run <prompt>` works when run
interactively (a TTY attached), but silently no-ops through the actual
adapter path -- plain `subprocess.run` with piped/captured stdout, no TTY,
exactly what _run_cli_lane below does. Observed: exit 0, no file edits,
zero normalized events, touched_files limited to opencode's own
`.git/opencode` cache. This is a real gap (opencode apparently needs a TTY
or a not-yet-wired headless flag), not fixed here -- flagging honestly
rather than leaving this adapter looking equivalent to the checked ones.
"""

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
    env: dict[str, str] | None = None,
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
        env=env,
        error_cls=OpenCodeAdapterError,
    )
