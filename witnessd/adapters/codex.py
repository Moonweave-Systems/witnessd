"""Codex CLI adapter for W4 runner lanes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from witnessd.adapters.base import AdapterResult
from witnessd.adapters.shell import TEST_STATUS_NOT_RUN, _diff_touched, _snapshot
from witnessd.events import encode_agent_event_jsonl, normalize_codex_jsonl_events

_OUTPUT_LIMIT = 4096


class CodexAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _resolve_codex(codex_binary: str) -> str:
    if os.path.sep in codex_binary or (
        os.path.altsep is not None and os.path.altsep in codex_binary
    ):
        path = Path(codex_binary)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        raise CodexAdapterError(
            "ERR_CODEX_UNAVAILABLE", f"codex binary is not executable: {codex_binary}"
        )

    resolved = shutil.which(codex_binary)
    if resolved is None:
        raise CodexAdapterError(
            "ERR_CODEX_UNAVAILABLE", f"codex binary not found: {codex_binary}"
        )
    return resolved


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


def _timeout_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


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


def run_codex_lane(
    *,
    sandbox: str,
    prompt: str,
    codex_binary: str = "codex",
    transcript_path: str,
    transcript_invocation_path: str | None = None,
    log_path: str | None = None,
    sandbox_mode: str = "workspace-write",
    allowed_touched_files: list[str] | None = None,
    timeout_seconds: int = 120,
    env: dict[str, str] | None = None,
) -> AdapterResult:
    if not prompt.strip():
        raise CodexAdapterError(
            "ERR_CODEX_PROMPT_MISSING", "codex prompt must not be empty"
        )
    if sandbox_mode == "workspace-write" and not allowed_touched_files:
        raise CodexAdapterError(
            "ERR_CODEX_ALLOWED_PATHS_REQUIRED",
            "workspace-write codex runs require predeclared allowed_touched_files",
        )

    repo = str(Path(sandbox).resolve(strict=False))
    codex = _resolve_codex(codex_binary)
    transcript = str(Path(transcript_path).resolve(strict=False))
    transcript_binding = transcript_invocation_path or transcript
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    normalized_transcript = str(Path(transcript).with_name("events.normalized.jsonl"))

    run_invocation = [
        codex,
        "--sandbox",
        sandbox_mode,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        repo,
        "-",
    ]
    evidence_invocation = list(run_invocation)

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            run_invocation,
            cwd=repo,
            env=env,
            input=prompt.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        raw_stdout = completed.stdout or b""
        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
        Path(transcript).write_bytes(raw_stdout)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        raw_stdout = _output_bytes(exc.stdout)
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_codex_jsonl_events(raw_stdout)
    Path(normalized_transcript).write_bytes(encode_agent_event_jsonl(normalized_events))

    if log_path is not None:
        _write_command_log(
            log_path,
            command=evidence_invocation,
            cwd=repo,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    after = _snapshot(repo)
    touched_files = _diff_touched(before, after)
    command_receipt: dict[str, Any] = {
        "command": evidence_invocation,
        "cwd": repo,
        "exit_code": exit_code,
        "stdout": stdout[:_OUTPUT_LIMIT],
        "stderr": stderr[:_OUTPUT_LIMIT],
    }

    return AdapterResult(
        adapter="codex",
        runner_kind="codex-cli",
        invocation=evidence_invocation,
        exit_code=exit_code,
        transcript_path=transcript_binding,
        command_receipts=[command_receipt],
        touched_files=touched_files,
        test_output={"status": TEST_STATUS_NOT_RUN},
        normalized_events=normalized_events,
    )


def _self_test() -> None:
    import stat
    import tempfile

    with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
        fake = Path(bindir) / "codex"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
            "while [ $# -gt 0 ]; do shift; done\n"
            "cat >/dev/null\n"
            "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
            "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"message\",\"text\":\"done\"}}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        transcript = Path(bindir) / "transcript.txt"
        result = run_codex_lane(
            sandbox=sandbox,
            prompt="self test",
            codex_binary=str(fake),
            transcript_path=str(transcript),
            sandbox_mode="read-only",
        )
        if result.runner_kind != "codex-cli":
            raise AssertionError("codex adapter must emit runner_kind=codex-cli")
        if "exec" not in result.invocation:
            raise AssertionError("codex invocation must use exec")
        if "--json" not in result.invocation:
            raise AssertionError("codex invocation must request JSONL events")
        if not transcript.exists():
            raise AssertionError("codex transcript must be written")
