"""Restart-safe lane scheduler backed by runlog projection."""

from __future__ import annotations

from typing import Any


class Scheduler:
    def __init__(self, event_log, run_id: str, concurrency: int = 1) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.event_log = event_log
        self.run_id = run_id
        self.concurrency = concurrency

    def reconcile(self) -> list[dict[str, Any]]:
        dispatched: dict[str, dict[str, Any]] = {}
        exited: set[str] = set()
        for record in self.event_log.read():
            if record.get("run_id") != self.run_id:
                continue
            payload = record.get("payload", {})
            lane_id = payload.get("lane_id")
            if not isinstance(lane_id, str):
                continue
            event = record.get("event")
            if event == "dispatch":
                dispatched[lane_id] = dict(payload)
            elif event == "exit":
                exited.add(lane_id)
        return [
            packet
            for lane_id, packet in dispatched.items()
            if lane_id not in exited
        ]

    def schedule(self, supervisor) -> list[Any]:
        from witnessd.pause import assert_not_paused

        assert_not_paused(self.event_log.read())
        handles = []
        for packet in self.reconcile()[: self.concurrency]:
            argv = packet.get("argv")
            if not isinstance(argv, list) or not argv:
                continue
            handles.append(
                supervisor.spawn(
                    lane_id=packet["lane_id"],
                    argv=[str(part) for part in argv],
                    runner_uid=packet.get("runner_uid"),
                    cwd=packet.get("cwd"),
                )
            )
        return handles


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog
    from witnessd.runlog import append_runlog

    with tempfile.TemporaryDirectory() as tmp:
        log = EventLog(os.path.join(tmp, "runlog.jsonl"))
        append_runlog(log, "R", "dispatch", payload={"lane_id": "L1"})
        append_runlog(log, "R", "exit", payload={"lane_id": "L1", "exit_code": 0})
        assert Scheduler(log, "R").reconcile() == []
