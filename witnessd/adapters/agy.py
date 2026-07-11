"""Antigravity CLI read-only review adapter for W4 lanes.

`--mode plan` is agy's own advisory read-only flag, not a hard guarantee:
live-verified that an edit-inducing prompt makes agy write to the sandbox
even with `--mode plan` set (agy 1.1.1). run_agy_review_lane() does not
trust that flag -- it independently snapshots the sandbox before/after and
fails closed (exit 125, test_output.status="failed") on ANY touched file,
regardless of what agy's own mode claims. See the touched_files check below.

agy also has no structured JSON event output (text/PTY transcript only);
normalize_agy_text_events() parses that text, and no attempt is made to
force a JSON event contract onto it.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import select
import subprocess
import time
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
from witnessd.events import encode_agent_event_jsonl, normalize_agy_text_events
from witnessd.model_declaration import (
    VERIFICATION_REQUESTED_UNVERIFIED,
    build_model_declaration,
)

_OUTPUT_LIMIT = 4096
_FORBIDDEN_FLAGS = frozenset(
    {
        "--dangerously-skip-permissions",
        "--approval-mode",
        "--output-format",
    }
)
_FINDING_RE = re.compile(
    r"^(?P<severity>low|medium|high|critical)\s+"
    r"(?P<file>\S+?):(?P<line>\d+)\s+(?P<summary>.+)$",
    re.IGNORECASE,
)


class AgyAdapterError(AdapterExecutionError):
    pass


class AgyCLIAdapter:
    provider = "google-antigravity"

    def __init__(self, *, agy_binary: str = "agy") -> None:
        self.agy_binary = agy_binary

    def compile_invocation(self, intent: RunIntent) -> list[str]:
        prompt = str(intent.get("prompt", "-"))
        print_timeout = intent.get("print_timeout")
        model = intent.get("model")
        add_dirs = intent.get("add_dirs")
        return _agy_invocation(
            self.agy_binary,
            prompt,
            print_timeout=str(print_timeout) if print_timeout is not None else None,
            model=str(model) if model is not None else None,
            add_dirs=(
                [str(item) for item in add_dirs] if isinstance(add_dirs, list) else None
            ),
        )

    def run(self, intent: RunIntent, sandbox: str) -> RawRun:
        invocation = self.compile_invocation(intent)
        completed = _run_pty_command(
            invocation,
            cwd=sandbox,
            timeout_seconds=int(intent.get("timeout_seconds", 120)),
            env=None,
        )
        return RawRun(
            invocation=invocation,
            exit_code=completed["exit_code"],
            raw_events=completed["raw_output"],
            stdout=completed["stdout"],
            stderr=completed["stderr"],
            effective_policy=self.effective_policy(
                RawRun(
                    invocation,
                    completed["exit_code"],
                    completed["raw_output"],
                    "",
                    "",
                )
            ),
        )

    def normalize(self, raw: RawRun):
        return normalize_agy_text_events(raw.raw_events)

    def effective_policy(self, raw: RawRun) -> dict[str, Any]:
        return {"mode": "plan", "output_format": "unconfirmed", "transport": "pty"}


def _agy_binary(path: str) -> str:
    try:
        return _resolve_executable(path, unavailable_code="ERR_AGY_UNAVAILABLE")
    except AdapterExecutionError as exc:
        raise AgyAdapterError(exc.code, exc.message) from exc


def _agy_invocation(
    agy_binary: str,
    prompt: str,
    *,
    print_timeout: str | None = None,
    model: str | None = None,
    add_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    _validate_extra_args(extra_args)
    invocation = [_agy_binary(agy_binary), "-p", prompt, "--mode", "plan", "--sandbox"]
    if print_timeout:
        invocation.extend(["--print-timeout", print_timeout])
    if model:
        invocation.extend(["--model", model])
    for directory in add_dirs or []:
        invocation.extend(["--add-dir", directory])
    if extra_args:
        invocation.extend(extra_args)
    return invocation


def _validate_extra_args(extra_args: list[str] | None) -> None:
    args = list(extra_args or [])
    for index, arg in enumerate(args):
        if arg in _FORBIDDEN_FLAGS:
            raise AgyAdapterError(
                "ERR_AGY_FORBIDDEN_FLAG",
                f"agy review lane forbids {arg}",
            )
        if arg == "--mode":
            value = args[index + 1] if index + 1 < len(args) else ""
            if value != "plan":
                raise AgyAdapterError(
                    "ERR_AGY_FORBIDDEN_FLAG",
                    "agy review lane requires --mode plan",
                )


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
        decoded_line = raw_line.decode("utf-8", errors="replace").strip()
        match = _FINDING_RE.match(decoded_line)
        if match is not None:
            findings.append(
                {
                    "severity": match.group("severity").lower(),
                    "file": match.group("file"),
                    "line": int(match.group("line")),
                    "summary": match.group("summary"),
                }
            )
            continue
        try:
            payload = json.loads(decoded_line)
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
        "provider": "google-antigravity",
        "axis": "review",
        "can_change_evidence_verdict": False,
        "invocation": invocation,
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "raw_output_text": raw_output.decode("utf-8", errors="replace"),
        "findings": findings,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_pty_command(
    invocation: list[str],
    *,
    cwd: str,
    timeout_seconds: int,
    env: dict[str, str] | None,
) -> dict[str, Any]:
    master_fd, slave_fd = os.openpty()
    raw_chunks: list[bytes] = []
    try:
        process = subprocess.Popen(
            invocation,
            cwd=cwd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=_merged_env(env),
        )
        os.close(slave_fd)
        slave_fd = -1
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            ready, _, _ = select.select([master_fd], [], [], min(0.1, remaining))
            if ready:
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                raw_chunks.append(chunk)
            if process.poll() is not None:
                ready, _, _ = select.select([master_fd], [], [], 0)
                if not ready:
                    break
        exit_code = 124 if timed_out else process.wait()
        if timed_out:
            process.wait()
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)

    raw_output = b"".join(raw_chunks)
    decoded = raw_output.decode("utf-8", errors="replace")
    return {
        "exit_code": exit_code,
        "raw_output": raw_output,
        "stdout": decoded,
        "stderr": "" if exit_code != 124 else "process timed out",
    }


def run_agy_review_lane(
    *,
    sandbox: str,
    prompt: str,
    agy_binary: str = "agy",
    transcript_path: str,
    review_receipt_path: str | None = None,
    log_path: str | None = None,
    timeout_seconds: int = 120,
    env: dict[str, str] | None = None,
    print_timeout: str | None = None,
    model: str | None = None,
    add_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> AdapterResult:
    if not prompt.strip():
        raise AgyAdapterError(
            "ERR_AGY_PROMPT_MISSING", "agy review prompt must not be empty"
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
        assert_evidence_path_separated(repo, evidence_path, error_cls=AgyAdapterError)
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    invocation = _agy_invocation(
        agy_binary,
        prompt,
        print_timeout=print_timeout,
        model=model,
        add_dirs=add_dirs,
        extra_args=extra_args,
    )

    before = _snapshot(repo)
    try:
        completed = _run_pty_command(
            invocation,
            cwd=repo,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        exit_code = completed["exit_code"]
        raw_stdout = completed["raw_output"]
        stdout = completed["stdout"]
        stderr = completed["stderr"]
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_agy_text_events(raw_stdout)
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
    # Hard enforcement, independent of agy's own --mode plan: any touched
    # file at all fails this lane closed. Do not weaken this to an allowlist
    # or "expected" edits -- a review lane has none, by contract.
    if touched_files:
        exit_code = 125
        message = "read-only review lane changed files"
        stderr = f"{stderr}\n{message}".strip()
        test_output = {"status": "failed", "summary": message}

    model_declaration = None
    if model is not None:
        # agy's --model has no rejection signal at all (live-verified: an
        # invalid model silently falls back to a default, no error, no exit
        # code change, and the transcript never names the model actually
        # used), so this can never honestly claim "verified" -- only that a
        # model was requested. Do not "upgrade" this to verified even if a
        # future agy version starts echoing the model; re-verify live first.
        model_declaration = build_model_declaration(
            adapter="agy",
            requested_model=model,
            verification_status=VERIFICATION_REQUESTED_UNVERIFIED,
        )

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
        adapter="agy",
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
        model_declaration=model_declaration,
    )
