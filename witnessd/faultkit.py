"""Deterministic W2 fault injection helpers."""

from __future__ import annotations

from witnessd.eventlog import EventLog


def zombie_hang(runlog_path: str, *, run_id: str = "faultkit-run", lane_id: str = "L1") -> None:
    log = EventLog(runlog_path)
    log.append(
        {
            "schema_version": "1.0",
            "kind": "witnessd-runlog-event",
            "run_id": run_id,
            "event": "spawn",
            "error_code": None,
            "ts_wall": "2026-01-01T00:00:00Z",
            "ts_monotonic": 0.0,
            "payload": {"lane_id": lane_id},
        }
    )
    log.append(
        {
            "schema_version": "1.0",
            "kind": "witnessd-runlog-event",
            "run_id": run_id,
            "event": "heartbeat",
            "error_code": None,
            "ts_wall": "2026-01-01T00:00:01Z",
            "ts_monotonic": 1.0,
            "payload": {"lane_id": lane_id},
        }
    )


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.liveness import HEARTBEAT_TTL_SECONDS, derive_liveness

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "runlog.jsonl")
        zombie_hang(path)
        state = derive_liveness(
            EventLog(path).read(),
            now_monotonic=HEARTBEAT_TTL_SECONDS + 2,
        )
        assert state == {"L1": "zombie"}
