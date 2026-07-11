"""Claude Code adapter for W4 runner lanes.

`--output-format stream-json` is required for claude to emit the structured
JSONL events normalize_claude_jsonl_events() parses -- without it, claude
only prints free text and there are no events to normalize. `--verbose` must
be passed alongside it: claude rejects `--print --output-format stream-json`
on its own with "Error: When using --print, --output-format=stream-json
requires --verbose" (live-verified against claude-code 2.1.207).
"""

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
    assert_evidence_path_separated,
)
from witnessd.adapters.shell import TEST_STATUS_NOT_RUN, _diff_touched, _snapshot
from witnessd.events import encode_agent_event_jsonl, normalize_claude_jsonl_events
from witnessd.model_declaration import (
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    build_model_declaration,
)


class ClaudeAdapterError(AdapterExecutionError):
    pass


class ClaudeCLIAdapter:
    provider = "claude-code"

    def __init__(self, *, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary

    def compile_invocation(self, intent: RunIntent) -> list[str]:
        prompt = str(intent.get("prompt", "-"))
        model = intent.get("model")
        return [
            _claude_binary(self.claude_binary),
            "-p",
            prompt,
            *(["--model", str(model)] if model else []),
            "--output-format",
            "stream-json",
            "--verbose",
        ]

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
            effective_policy=self.effective_policy(
                RawRun(
                    invocation, completed.returncode, completed.stdout or b"", "", ""
                )
            ),
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


def _claude_model_rejection(raw_jsonl: bytes) -> str | None:
    """Scan for claude's own signal that the requested model was rejected
    (live-verified against claude-code 2.1.207 through the actual
    subprocess.run adapter path -- not just a manual terminal check, which
    first suggested a narrower shape than what actually happens): the
    process exit code is not a reliable signal by itself (observed both 0
    and 1 for the same rejection across separate runs), and the specific
    `error: "model_not_found"` code was found on the "assistant" message
    event, not the terminal "result" event as first assumed -- so this scans
    every event, not just "result". Narrow, exact match on that specific
    error code on purpose: claude's is_error can also fire for unrelated API
    errors, and only a model-rejection-specific signal should escalate this
    lane closed.
    """
    for line in raw_jsonl.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error") == "model_not_found":
            result_text = payload.get("result")
            if isinstance(result_text, str):
                return result_text
            message = payload.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        return item["text"]
            return "model_not_found"
    return None


def run_claude_lane(
    *,
    sandbox: str,
    prompt: str,
    claude_binary: str = "claude",
    transcript_path: str,
    log_path: str | None = None,
    timeout_seconds: int = 120,
    model: str | None = None,
) -> AdapterResult:
    if not prompt.strip():
        raise ClaudeAdapterError(
            "ERR_CLAUDE_PROMPT_MISSING", "claude prompt must not be empty"
        )
    repo = str(Path(sandbox).resolve(strict=False))
    transcript = str(Path(transcript_path).resolve(strict=False))
    normalized_transcript = str(Path(transcript).with_name("events.normalized.jsonl"))
    evidence_paths = [
        transcript,
        normalized_transcript,
        *([log_path] if log_path is not None else []),
    ]
    for evidence_path in evidence_paths:
        assert_evidence_path_separated(
            repo, evidence_path, error_cls=ClaudeAdapterError
        )
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    invocation = [
        _claude_binary(claude_binary),
        "-p",
        prompt,
        *(["--model", model] if model else []),
        "--output-format",
        "stream-json",
        "--verbose",
    ]

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
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else ""
        )
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_claude_jsonl_events(raw_stdout)
    Path(normalized_transcript).write_bytes(encode_agent_event_jsonl(normalized_events))
    test_output: dict[str, Any] = {"status": TEST_STATUS_NOT_RUN}
    model_declaration = None
    if model is not None:
        rejection = _claude_model_rejection(raw_stdout)
        if rejection is not None:
            exit_code = 125
            message = f"requested model {model} rejected: {rejection}"
            stderr = f"{stderr}\n{message}".strip()
            test_output = {"status": "failed", "summary": message}
            model_declaration = build_model_declaration(
                adapter="claude",
                requested_model=model,
                verification_status=VERIFICATION_REJECTED,
                detail=rejection,
            )
        else:
            model_declaration = build_model_declaration(
                adapter="claude",
                requested_model=model,
                verification_status=VERIFICATION_VERIFIED,
            )

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
        touched_files=_diff_touched(
            before, after, sandbox=repo, evidence_paths=evidence_paths
        ),
        test_output=test_output,
        normalized_events=normalized_events,
        raw_events_path=transcript,
        normalized_events_path=normalized_transcript,
        model_declaration=model_declaration,
    )
