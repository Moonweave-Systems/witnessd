"""Gemini CLI read-only review adapter for W4 lanes."""

from __future__ import annotations

import hashlib
import json
import os
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
from witnessd.events import encode_agent_event_jsonl, normalize_gemini_jsonl_events

_OUTPUT_LIMIT = 4096


class GeminiAdapterError(AdapterExecutionError):
    pass


class GeminiCLIAdapter:
    provider = "google-gemini"

    def __init__(self, *, gemini_binary: str = "gemini") -> None:
        self.gemini_binary = gemini_binary

    def compile_invocation(self, intent: RunIntent) -> list[str]:
        prompt = str(intent.get("prompt", "-"))
        return _gemini_invocation(self.gemini_binary, prompt)

    def run(self, intent: RunIntent, sandbox: str) -> RawRun:
        invocation = self.compile_invocation(intent)
        completed = subprocess.run(
            invocation,
            cwd=sandbox,
            capture_output=True,
            check=False,
        )
        raw_stdout = completed.stdout or b""
        return RawRun(
            invocation=invocation,
            exit_code=completed.returncode,
            raw_events=raw_stdout,
            stdout=raw_stdout.decode("utf-8", errors="replace"),
            stderr=(completed.stderr or b"").decode("utf-8", errors="replace"),
            effective_policy=self.effective_policy(
                RawRun(invocation, completed.returncode, raw_stdout, "", "")
            ),
        )

    def normalize(self, raw: RawRun):
        return normalize_gemini_jsonl_events(raw.raw_events)

    def effective_policy(self, raw: RawRun) -> dict[str, Any]:
        return {"approval_mode": "plan", "output_format": "stream-json"}


def _gemini_binary(path: str) -> str:
    try:
        return _resolve_executable(path, unavailable_code="ERR_GEMINI_UNAVAILABLE")
    except AdapterExecutionError as exc:
        raise GeminiAdapterError(exc.code, exc.message) from exc


def _gemini_invocation(gemini_binary: str, prompt: str) -> list[str]:
    return [
        _gemini_binary(gemini_binary),
        "--approval-mode",
        "plan",
        "--output-format",
        "stream-json",
        "-p",
        prompt,
    ]


def _decode_output(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _output_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return b""


def _merged_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = os.environ.copy()
    merged.update(env)
    return merged


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


def _coerce_finding(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    severity = value.get("severity")
    file_path = value.get("file")
    line = value.get("line")
    summary = value.get("summary")
    if (
        not isinstance(severity, str)
        or not isinstance(file_path, str)
        or not isinstance(summary, str)
    ):
        return None
    if not isinstance(line, int):
        line = None
    return {
        "severity": severity,
        "file": file_path,
        "line": line,
        "summary": summary,
    }


def _findings_from_text(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    findings: list[dict[str, Any]] = []
    for item in parsed:
        finding = _coerce_finding(item)
        if finding is not None:
            findings.append(finding)
    return findings


def _extract_findings(raw_output: bytes) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw_line in raw_output.splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        direct = _coerce_finding(payload)
        if direct is not None:
            findings.append(direct)
            continue
        for key in ("findings", "text", "content", "response"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    finding = _coerce_finding(item)
                    if finding is not None:
                        findings.append(finding)
            elif isinstance(value, str):
                findings.extend(_findings_from_text(value))
    return findings


def _write_review_receipt(
    path: str,
    *,
    invocation: list[str],
    raw_output: bytes,
    findings: list[dict[str, Any]],
) -> None:
    receipt = {
        "kind": "moonweave-review-receipt",
        "schema_version": "1.0",
        "provider": "google-gemini",
        "axis": "review",
        "can_change_evidence_verdict": False,
        "invocation": invocation,
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "findings": findings,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_gemini_review_lane(
    *,
    sandbox: str,
    prompt: str,
    gemini_binary: str = "gemini",
    transcript_path: str,
    review_receipt_path: str | None = None,
    log_path: str | None = None,
    timeout_seconds: int = 120,
    env: dict[str, str] | None = None,
) -> AdapterResult:
    if not prompt.strip():
        raise GeminiAdapterError(
            "ERR_GEMINI_PROMPT_MISSING", "gemini review prompt must not be empty"
        )

    repo = str(Path(sandbox).resolve(strict=False))
    transcript = str(Path(transcript_path).resolve(strict=False))
    normalized_transcript = str(Path(transcript).with_name("events.normalized.jsonl"))
    review_receipt = str(
        Path(review_receipt_path).resolve(strict=False)
        if review_receipt_path is not None
        else Path(transcript).with_name("review-receipt.json")
    )
    evidence_paths = [
        transcript,
        normalized_transcript,
        review_receipt,
        *([log_path] if log_path is not None else []),
    ]
    for evidence_path in evidence_paths:
        assert_evidence_path_separated(
            repo, evidence_path, error_cls=GeminiAdapterError
        )
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    invocation = _gemini_invocation(gemini_binary, prompt)

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            invocation,
            cwd=repo,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=_merged_env(env),
        )
        exit_code = completed.returncode
        raw_stdout = completed.stdout or b""
        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
        Path(transcript).write_bytes(raw_stdout)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        raw_stdout = _output_bytes(exc.stdout)
        stdout = _decode_output(raw_stdout)
        stderr = _decode_output(exc.stderr)
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_gemini_jsonl_events(raw_stdout)
    Path(normalized_transcript).write_bytes(encode_agent_event_jsonl(normalized_events))
    findings = _extract_findings(raw_stdout)
    _write_review_receipt(
        review_receipt,
        invocation=invocation,
        raw_output=raw_stdout,
        findings=findings,
    )

    after = _snapshot(repo)
    touched_files = _diff_touched(
        before, after, sandbox=repo, evidence_paths=evidence_paths
    )
    test_output: dict[str, Any] = {"status": TEST_STATUS_NOT_RUN}
    if touched_files:
        exit_code = 125
        message = "read-only review lane changed files"
        stderr = f"{stderr}\n{message}".strip()
        test_output = {"status": "failed", "summary": message}

    if log_path is not None:
        _write_command_log(
            log_path,
            command=invocation,
            cwd=repo,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    return AdapterResult(
        adapter="gemini",
        runner_kind="manual",
        invocation=invocation,
        exit_code=exit_code,
        transcript_path=transcript,
        command_receipts=[
            {
                "command": invocation,
                "cwd": repo,
                "exit_code": exit_code,
                "stdout": stdout[:_OUTPUT_LIMIT],
                "stderr": stderr[:_OUTPUT_LIMIT],
            }
        ],
        touched_files=touched_files,
        test_output=test_output,
        normalized_events=normalized_events,
        raw_events_path=transcript,
        normalized_events_path=normalized_transcript,
        review_receipt_path=review_receipt,
    )
