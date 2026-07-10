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
    CHECKPOINT_SCHEMA_VERSION = "1.0"

    def __init__(self, path: str) -> None:
        self.path = path
        self._seq = 0
        self._prev_event_hash: str | None = None
        checkpoint = self._load_checkpoint()
        if checkpoint is None:
            existing = self.read()
            self._set_state_from_records(existing)
            if existing:
                self._write_checkpoint(existing[-1])
            return
        self._seq = int(checkpoint["seq"]) + 1
        self._prev_event_hash = str(checkpoint["event_hash"])

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(_lock_path(self.path), "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            checkpoint = self._checkpoint_for_append()
            seq = int(checkpoint["seq"]) + 1 if checkpoint is not None else 0
            prev = str(checkpoint["event_hash"]) if checkpoint is not None else None
            record = dict(event)
            record["seq"] = seq
            record["prev_event_hash"] = prev
            record["event_hash"] = canonical_hash(
                {key: value for key, value in record.items() if key != "event_hash"}
            )
            self._append_record(record)
            self._write_checkpoint(record)
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
        self._set_state_from_records(records)
        if records:
            self._write_checkpoint(records[-1])
        return records

    def _checkpoint_for_append(self) -> dict[str, Any] | None:
        checkpoint = self._load_checkpoint()
        if checkpoint is None:
            records = self.read()
            return self._checkpoint_from_record(records[-1]) if records else None

        try:
            current_size = os.path.getsize(self.path)
        except FileNotFoundError:
            if int(checkpoint["seq"]) == -1:
                return None
            raise EventLogIntegrityError(None)

        if current_size != int(checkpoint["offset"]):
            records = self.read()
            return self._checkpoint_from_record(records[-1]) if records else None

        last_record = _read_last_record(self.path)
        if last_record is None:
            if int(checkpoint["seq"]) == -1:
                return None
            raise EventLogIntegrityError(None)
        if (
            int(last_record.get("seq", -1)) != int(checkpoint["seq"])
            or last_record.get("event_hash") != checkpoint["event_hash"]
            or canonical_hash(
                {key: value for key, value in last_record.items() if key != "event_hash"}
            )
            != last_record.get("event_hash")
        ):
            raise EventLogIntegrityError(int(checkpoint["seq"]))
        return checkpoint

    def _checkpoint_path(self) -> str:
        digest = canonical_hash(os.path.abspath(self.path))[:32]
        checkpoint_dir = os.path.join(tempfile.gettempdir(), "witnessd-eventlog-checkpoints")
        os.makedirs(checkpoint_dir, exist_ok=True)
        return os.path.join(checkpoint_dir, f"{digest}.checkpoint.json")

    def _load_checkpoint(self) -> dict[str, Any] | None:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self._checkpoint_path(), "r", encoding="utf-8") as handle:
                checkpoint = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise EventLogIntegrityError(None) from exc

        if checkpoint.get("schema_version") != self.CHECKPOINT_SCHEMA_VERSION:
            return None
        checkpoint_hash = checkpoint.get("checkpoint_hash")
        expected = canonical_hash(
            {key: value for key, value in checkpoint.items() if key != "checkpoint_hash"}
        )
        if checkpoint_hash != expected:
            raise EventLogIntegrityError(None)
        return checkpoint

    def _write_checkpoint(self, record: dict[str, Any]) -> None:
        checkpoint = self._checkpoint_from_record(record)
        checkpoint["checkpoint_hash"] = canonical_hash(checkpoint)
        _atomic_write_json(self._checkpoint_path(), checkpoint)

    def _checkpoint_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": self.CHECKPOINT_SCHEMA_VERSION,
            "kind": "witnessd-eventlog-checkpoint",
            "path": os.path.abspath(self.path),
            "offset": os.path.getsize(self.path),
            "seq": int(record.get("seq", -1)),
            "event_hash": record.get("event_hash"),
        }

    def _append_record(self, record: dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(directory)

    def _set_state_from_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            self._seq = 0
            self._prev_event_hash = None
            return
        self._seq = int(records[-1].get("seq", -1)) + 1
        last_hash = records[-1].get("event_hash")
        self._prev_event_hash = last_hash if isinstance(last_hash, str) else None


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


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
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


def _read_last_record(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            if end == 0:
                return None
            position = end
            chunks: list[bytes] = []
            while position > 0:
                read_size = min(4096, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                chunks.append(chunk)
                data = b"".join(reversed(chunks))
                lines = [line for line in data.splitlines() if line.strip()]
                if len(lines) >= 2 or position == 0:
                    return json.loads(lines[-1].decode("utf-8")) if lines else None
    except FileNotFoundError:
        return None
    return None
