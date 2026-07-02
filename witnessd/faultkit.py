"""Deterministic W2 fault injection helpers."""

from __future__ import annotations

import json
import shutil

from witnessd.eventlog import EventLog


def zombie_hang(
    runlog_path: str, *, run_id: str = "faultkit-run", lane_id: str = "L1"
) -> None:
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


def crash_mid_toolcall(
    *,
    runlog_before_path: str,
    runlog_after_path: str,
    session_path: str,
    run_id: str = "faultkit-resume-run",
    lane_id: str = "L1",
) -> dict:
    """Write a deterministic interrupted-toolcall resume fixture.

    The "before" log stops after a tool call has started. The "after" log is the
    same chain continued with a resume event, proving that resume keeps the
    cursor and appends rather than replaying already-started work.
    """

    log = EventLog(runlog_after_path)
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
            "event": "tool-call-start",
            "error_code": None,
            "ts_wall": "2026-01-01T00:00:01Z",
            "ts_monotonic": 1.0,
            "payload": {"lane_id": lane_id, "tool_call_cursor": 1},
        }
    )
    shutil.copyfile(runlog_after_path, runlog_before_path)
    resume = log.append(
        {
            "schema_version": "1.0",
            "kind": "witnessd-runlog-event",
            "run_id": run_id,
            "event": "resume",
            "error_code": None,
            "ts_wall": "2026-01-01T00:00:02Z",
            "ts_monotonic": 2.0,
            "payload": {
                "lane_id": lane_id,
                "run_state": "evidence-pending",
                "tool_call_cursor": 1,
                "idempotency_reapplied": 0,
            },
        }
    )
    state = {
        "run_id": run_id,
        "lane_id": lane_id,
        "run_state": "evidence-pending",
        "tool_call_cursor": 1,
        "last_seq": resume["seq"],
        "last_event_hash": resume["event_hash"],
        "idempotency_reapplied": 0,
    }
    with open(session_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    return state


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

        before = os.path.join(tmp, "before.jsonl")
        after = os.path.join(tmp, "after.jsonl")
        session = os.path.join(tmp, "session.json")
        resumed = crash_mid_toolcall(
            runlog_before_path=before,
            runlog_after_path=after,
            session_path=session,
        )
        assert resumed["run_state"] == "evidence-pending"
        assert resumed["idempotency_reapplied"] == 0
