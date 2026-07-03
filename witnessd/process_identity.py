"""Process identity helpers for PID-reuse-safe runlog projections."""

from __future__ import annotations

import os
import subprocess

_PS_TIMEOUT_SECONDS = 1.0


def _ps_field(pid: int, field: str) -> str | None:
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return None
    if normalized_pid <= 0:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(normalized_pid)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=_PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def read_pid_start_time(pid: int) -> str | None:
    """Return an opaque process start token for pid, or None when unavailable."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().split()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return _ps_field(pid, "lstart")
    return fields[21] if len(fields) > 21 else None


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_state(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().split()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        state = _ps_field(pid, "state")
        return state[0] if state else None
    return fields[2] if len(fields) > 2 else None


def pid_identity_matches(pid: int, expected_start_time: str | None) -> bool:
    if not expected_start_time:
        return False
    return read_pid_start_time(pid) == expected_start_time
