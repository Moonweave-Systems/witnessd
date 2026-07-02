"""Worker supervisor: child-process exit and heartbeat events via runlog.

This module deliberately supervises OS child processes directly. It never uses
terminal text or worker self-report text as a completion signal.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Sequence

from witnessd.process_identity import read_pid_start_time
from witnessd.runlog import append_runlog


class RegionLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerHandle:
    pid: int
    lane_id: str
    runner_uid: int | None
    popen: subprocess.Popen[bytes]


class WorkerSupervisor:
    def __init__(self, event_log, run_id: str) -> None:
        self.event_log = event_log
        self.run_id = run_id
        self._handles: list[WorkerHandle] = []
        self._claimed_paths: dict[str, str] = {}
        try:
            signal.signal(signal.SIGCHLD, self._on_sigchld)
        except (AttributeError, ValueError):
            # SIGCHLD is Unix/main-thread only. wait() remains the source of
            # deterministic reaping in tests and normal operation.
            pass

    def _on_sigchld(self, _signum, _frame) -> None:
        return None

    def spawn(
        self,
        *,
        lane_id: str,
        argv: Sequence[str],
        runner_uid: int | None,
        cwd: str | None = None,
    ) -> WorkerHandle:
        from witnessd.pause import assert_not_paused

        assert_not_paused(self.event_log.read())
        preexec_fn = None
        if runner_uid is not None and hasattr(os, "setuid") and os.geteuid() == 0:
            preexec_fn = lambda: os.setuid(runner_uid)

        popen = subprocess.Popen(
            list(argv),
            cwd=cwd,
            preexec_fn=preexec_fn,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        handle = WorkerHandle(
            pid=popen.pid,
            lane_id=lane_id,
            runner_uid=runner_uid,
            popen=popen,
        )
        self._handles.append(handle)
        append_runlog(
            self.event_log,
            self.run_id,
            "spawn",
            payload={
                "lane_id": lane_id,
                "pid": handle.pid,
                "pid_start_time": read_pid_start_time(handle.pid),
                "runner_uid": runner_uid,
            },
        )
        return handle

    def handles(self) -> list[WorkerHandle]:
        return list(self._handles)

    def heartbeat(self, handle: WorkerHandle) -> dict:
        return append_runlog(
            self.event_log,
            self.run_id,
            "heartbeat",
            payload={"lane_id": handle.lane_id, "pid": handle.pid},
        )

    def wait(self, handle: WorkerHandle) -> int:
        exit_code = handle.popen.wait()
        append_runlog(
            self.event_log,
            self.run_id,
            "exit",
            payload={
                "lane_id": handle.lane_id,
                "pid": handle.pid,
                "exit_code": int(exit_code),
            },
        )
        self._handles = [candidate for candidate in self._handles if candidate != handle]
        return int(exit_code)

    def claim_region(self, lane_id: str, paths: Sequence[str]) -> dict:
        for path in paths:
            owner = self._claimed_paths.get(path)
            if owner is not None and owner != lane_id:
                raise RegionLockError(f"region already claimed: {path}")
        for path in paths:
            self._claimed_paths[path] = lane_id
        return append_runlog(
            self.event_log,
            self.run_id,
            "claim",
            payload={"lane_id": lane_id, "paths": list(paths)},
        )

    def release_region(self, lane_id: str, paths: Sequence[str]) -> dict:
        for path in paths:
            if self._claimed_paths.get(path) == lane_id:
                del self._claimed_paths[path]
        return append_runlog(
            self.event_log,
            self.run_id,
            "release",
            payload={"lane_id": lane_id, "paths": list(paths)},
        )


def _self_test() -> None:
    import tempfile

    from witnessd.eventlog import EventLog

    with tempfile.TemporaryDirectory() as tmp:
        log = EventLog(os.path.join(tmp, "runlog.jsonl"))
        supervisor = WorkerSupervisor(log, run_id="R")
        handle = supervisor.spawn(
            lane_id="L1",
            argv=["sh", "-c", "exit 0"],
            runner_uid=os.getuid(),
        )
        assert supervisor.wait(handle) == 0
        assert any(record["event"] == "exit" for record in log.read())
