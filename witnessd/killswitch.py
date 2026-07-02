"""W5 kill-switch: terminate supervised children and leave runlog evidence."""

from __future__ import annotations

import signal
import time
from typing import Any

from witnessd.runlog import append_runlog

ERR_WITNESSD_KILL_UNCONFIRMED = "ERR_WITNESSD_KILL_UNCONFIRMED"
ERR_WITNESSD_KILL_NO_TARGETS = "ERR_WITNESSD_KILL_NO_TARGETS"
_TERM_GRACE_SECONDS = 2.0


def _terminate(handle, grace: float) -> tuple[bool, int | None]:
    popen = handle.popen
    if popen.poll() is not None:
        return True, popen.returncode
    popen.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if popen.poll() is not None:
            return True, popen.returncode
        time.sleep(0.02)
    popen.send_signal(signal.SIGKILL)
    try:
        popen.wait(timeout=grace)
    except Exception:
        pass
    code = popen.poll()
    return code is not None, code


def kill_all(supervisor, log, run_id: str, grace: float = _TERM_GRACE_SECONDS) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    handles = list(supervisor.handles())
    if not handles:
        append_runlog(
            log,
            run_id,
            "kill",
            error_code=ERR_WITNESSD_KILL_NO_TARGETS,
            payload={"outcomes": outcomes, "all_confirmed_dead": False},
        )
        return {
            "killed": False,
            "all_confirmed_dead": False,
            "outcomes": outcomes,
            "error_code": ERR_WITNESSD_KILL_NO_TARGETS,
        }
    all_dead = True
    for handle in handles:
        confirmed, code = _terminate(handle, grace)
        outcomes.append(
            {
                "lane_id": handle.lane_id,
                "pid": handle.pid,
                "confirmed_dead": confirmed,
                "exit_code": code,
            }
        )
        if confirmed:
            append_runlog(
                log,
                run_id,
                "exit",
                payload={
                    "lane_id": handle.lane_id,
                    "pid": handle.pid,
                    "exit_code": int(code) if code is not None else -9,
                },
            )
        else:
            all_dead = False
    append_runlog(
        log,
        run_id,
        "kill",
        error_code=None if all_dead else ERR_WITNESSD_KILL_UNCONFIRMED,
        payload={"outcomes": outcomes, "all_confirmed_dead": all_dead},
    )
    result = {"killed": True, "all_confirmed_dead": all_dead, "outcomes": outcomes}
    if not all_dead:
        result["error_code"] = ERR_WITNESSD_KILL_UNCONFIRMED
    return result


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog
    from witnessd.liveness import derive_liveness
    from witnessd.supervisor import WorkerSupervisor

    with tempfile.TemporaryDirectory() as tmp:
        log = EventLog(os.path.join(tmp, "runlog.jsonl"))
        supervisor = WorkerSupervisor(log, run_id="R")
        supervisor.spawn(lane_id="L1", argv=["sh", "-c", "sleep 30"], runner_uid=os.getuid())
        result = kill_all(supervisor, log, "R", grace=0.05)
        assert result["all_confirmed_dead"] is True
        assert derive_liveness(log.read(), now_monotonic=10**12)["L1"] == "dead"
