"""§6.0.3 runlog records — spawn/heartbeat/exit/resume events on the EventLog.

Every run-state transition is appended to the W1 EventLog as a
`witnessd-runlog-event` (the runlog chain). This chain is SEPARATE from the
capture-manifest chain and is NOT an input to Depone's `verify_capture_chain`;
its integrity is checked here by `verify_runlog`. Run-state and liveness are
pure projections of these records.
"""

from __future__ import annotations

import time
from typing import Any

from witnessd.canonical import canonical_hash

RUNLOG_SCHEMA_VERSION = "1.0"
RUNLOG_KIND = "witnessd-runlog-event"


def _rfc3339(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def build_runlog_body(
    run_id: str,
    event: str,
    error_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    return {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "kind": RUNLOG_KIND,
        "run_id": run_id,
        "event": event,
        "error_code": error_code,
        "ts_wall": _rfc3339(now),
        "ts_monotonic": time.monotonic(),
        "payload": payload or {},
    }


def append_runlog(
    log,
    run_id: str,
    event: str,
    error_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return log.append(build_runlog_body(run_id, event, error_code, payload))


def event_hash(record: dict[str, Any]) -> str:
    return canonical_hash(
        {key: value for key, value in record.items() if key != "event_hash"}
    )


def verify_runlog(records: list[dict[str, Any]]) -> dict[str, Any]:
    prev: str | None = None
    for index, record in enumerate(records):
        if record.get("event_hash") != event_hash(record):
            return {"ok": False, "broken_at": index}
        if record.get("prev_event_hash") != prev:
            return {"ok": False, "broken_at": index}
        prev = record["event_hash"]
    return {"ok": True, "broken_at": None}


def _self_test() -> None:
    body = build_runlog_body("R", "spawn", payload={"lane_id": "L1"})
    body["seq"] = 0
    body["prev_event_hash"] = None
    body["event_hash"] = event_hash(body)
    assert verify_runlog([body]) == {"ok": True, "broken_at": None}
