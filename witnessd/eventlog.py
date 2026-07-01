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
from typing import Any

from witnessd.canonical import canonical_hash


class EventLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._seq = 0
        self._prev_event_hash: str | None = None

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        record = dict(event)
        record["seq"] = self._seq
        record["prev_event_hash"] = self._prev_event_hash
        record["event_hash"] = canonical_hash(
            {key: value for key, value in record.items() if key != "event_hash"}
        )
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._seq += 1
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
