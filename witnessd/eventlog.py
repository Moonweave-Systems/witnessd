"""Append-only, hash-chained runlog — the sole write point to the SoT.

The Evidence Emitter routes every state-changing event through an EventLog so
the run's source-of-truth is an append-only signed event stream; run-state and
status are read-only projections of it (never written directly).

This runlog chain is SEPARATE from the capture-manifest chain (which links via
`prev_capture_hash` and is Depone's concern). Runlog events carry
`kind == "witnessd-runlog-event"` and link via `prev_event_hash`: the canonical
hash of the immediately preceding event, or None for the genesis event.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from witnessd.canonical import canonical_hash
from witnessd.runlog import verify_runlog


class EventLogIntegrityError(RuntimeError):
    def __init__(self, broken_at: int | None) -> None:
        super().__init__(f"runlog chain verification failed at {broken_at}")
        self.broken_at = broken_at


class EventLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._seq = 0
        self._prev_event_hash: str | None = None
        existing = self.read()
        if existing:
            self._seq = int(existing[-1].get("seq", -1)) + 1
            last_hash = existing[-1].get("event_hash")
            self._prev_event_hash = last_hash if isinstance(last_hash, str) else None

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(_lock_path(self.path), "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            existing = self.read()
            if existing:
                seq = int(existing[-1].get("seq", -1)) + 1
                prev_event_hash = existing[-1].get("event_hash")
                prev = prev_event_hash if isinstance(prev_event_hash, str) else None
            else:
                seq = 0
                prev = None
            record = dict(event)
            record["seq"] = seq
            record["prev_event_hash"] = prev
            record["event_hash"] = canonical_hash(
                {key: value for key, value in record.items() if key != "event_hash"}
            )
            _atomic_write_records(self.path, [*existing, record])
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self._seq = record["seq"] + 1
        self._prev_event_hash = record["event_hash"]
        return record

    def read(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        records.append(json.loads(line))
        except FileNotFoundError:
            return []
        verification = verify_runlog(records)
        if not verification["ok"]:
            raise EventLogIntegrityError(verification["broken_at"])
        return records


def _lock_path(path: str) -> str:
    digest = canonical_hash(os.path.abspath(path))[:32]
    lock_dir = os.path.join(tempfile.gettempdir(), "witnessd-eventlog-locks")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f"{digest}.lock")


def _atomic_write_records(path: str, records: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(directory)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(directory: str) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
