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


def run_codex_lane(
    *,
    sandbox: str,
    prompt: str,
    codex_binary: str = "codex",
    transcript_path: str,
    log_path: str | None = None,
    sandbox_mode: str = "workspace-write",
    timeout_seconds: int = 120,
) -> AdapterResult:
    if not prompt.strip():
        raise CodexAdapterError(
            "ERR_CODEX_PROMPT_MISSING", "codex prompt must not be empty"
        )

    repo = str(Path(sandbox).resolve(strict=False))
    codex = _resolve_codex(codex_binary)
    transcript = str(Path(transcript_path).resolve(strict=False))
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)

    invocation = [
        codex,
        "--sandbox",
        sandbox_mode,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        repo,
        "--output-last-message",
        transcript,
        "-",
    ]

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            invocation,
            cwd=repo,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
    except OSError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)

    if log_path is not None:
        _write_command_log(
            log_path,
            command=invocation,
            cwd=repo,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    if not Path(transcript).exists():
        Path(transcript).write_text((stdout or "") + (stderr or ""), encoding="utf-8")

    after = _snapshot(repo)
    touched_files = _diff_touched(before, after)
    command_receipt: dict[str, Any] = {
        "command": invocation,
        "cwd": repo,
        "exit_code": exit_code,
        "stdout": stdout[:_OUTPUT_LIMIT],
        "stderr": stderr[:_OUTPUT_LIMIT],
    }

    return AdapterResult(
        adapter="codex",
        runner_kind="codex-cli",
        invocation=invocation,
        exit_code=exit_code,
        transcript_path=transcript,
        command_receipts=[command_receipt],
        touched_files=touched_files,
        test_output={"status": TEST_STATUS_NOT_RUN},
    )


def _self_test() -> None:
    import stat
    import tempfile

    with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
        fake = Path(bindir) / "codex"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
            "out=\"\"\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
            "  shift\n"
            "done\n"
            ": > \"$out\"\n"
            "echo done >> \"$out\"\n"
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
        )
        if result.runner_kind != "codex-cli":
            raise AssertionError("codex adapter must emit runner_kind=codex-cli")
        if "exec" not in result.invocation:
            raise AssertionError("codex invocation must use exec")
        if not transcript.exists():
            raise AssertionError("codex transcript must be written")
