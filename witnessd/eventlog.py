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
from typing import Any
try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from witnessd.canonical import canonical_hash


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
        with open(self.path, "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            existing = [
                json.loads(line)
                for line in handle
                if line.strip()
            ]
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
            line = json.dumps(record, sort_keys=True, separators=(",", ":"))
            handle.seek(0, 2)
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
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
        return records
