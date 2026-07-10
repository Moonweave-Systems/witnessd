"""Claude Code adapter for W4 runner lanes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from witnessd.adapters.base import (
    AdapterExecutionError,
    AdapterResult,
    RawRun,
    RunIntent,
    _resolve_executable,
)
from witnessd.adapters.shell import TEST_STATUS_NOT_RUN, _diff_touched, _snapshot
from witnessd.events import encode_agent_event_jsonl, normalize_claude_jsonl_events


class ClaudeAdapterError(AdapterExecutionError):
    pass


class ClaudeCLIAdapter:
    provider = "claude-code"

    def __init__(self, *, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary

    def compile_invocation(self, intent: RunIntent) -> list[str]:
        prompt = str(intent.get("prompt", "-"))
        return [_claude_binary(self.claude_binary), "-p", prompt]

    def run(self, intent: RunIntent, sandbox: str) -> RawRun:
        invocation = self.compile_invocation(intent)
        completed = subprocess.run(
            invocation,
            cwd=sandbox,
            text=False,
            capture_output=True,
            check=False,
        )
        return RawRun(
            invocation=invocation,
            exit_code=completed.returncode,
            raw_events=completed.stdout or b"",
            stdout=(completed.stdout or b"").decode("utf-8", errors="replace"),
            stderr=(completed.stderr or b"").decode("utf-8", errors="replace"),
            effective_policy=self.effective_policy(RawRun(invocation, completed.returncode, completed.stdout or b"", "", "")),
        )

    def normalize(self, raw: RawRun):
        return normalize_claude_jsonl_events(raw.raw_events)

    def effective_policy(self, raw: RawRun) -> dict[str, Any]:
        return {}


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
    repo = str(Path(sandbox).resolve(strict=False))
    transcript = str(Path(transcript_path).resolve(strict=False))
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    normalized_transcript = str(Path(transcript).with_name("events.normalized.jsonl"))
    invocation = [_claude_binary(claude_binary), "-p", prompt]

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            invocation,
            cwd=repo,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        raw_stdout = completed.stdout or b""
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        Path(transcript).write_bytes(raw_stdout)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        raw_stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else ""
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_claude_jsonl_events(raw_stdout)
    Path(normalized_transcript).write_bytes(encode_agent_event_jsonl(normalized_events))
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            json.dumps(
                {
                    "command": invocation,
                    "cwd": repo,
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
    after = _snapshot(repo)
    return AdapterResult(
        adapter="claude",
        runner_kind="manual",
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
        normalized_events=normalized_events,
        raw_events_path=transcript,
        normalized_events_path=normalized_transcript,
    )
