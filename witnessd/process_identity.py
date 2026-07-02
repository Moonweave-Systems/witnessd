"""Process identity helpers for PID-reuse-safe runlog projections."""

from __future__ import annotations

import os


def read_pid_start_time(pid: int) -> str | None:
    """Return Linux /proc starttime for pid, or None when unavailable."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().split()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
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
        return None
    return fields[2] if len(fields) > 2 else None


def pid_identity_matches(pid: int, expected_start_time: str | None) -> bool:
    if not expected_start_time:
        return False
    return read_pid_start_time(pid) == expected_start_time
