"""W5 kill-switch: terminate supervised children and leave runlog evidence."""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from typing import Any

from witnessd.process_identity import (
    pid_identity_matches,
    process_exists,
    process_state,
)
from witnessd.runlog import append_runlog

ERR_WITNESSD_KILL_UNCONFIRMED = "ERR_WITNESSD_KILL_UNCONFIRMED"
ERR_WITNESSD_KILL_NO_TARGETS = "ERR_WITNESSD_KILL_NO_TARGETS"
_TERM_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class KillTarget:
    lane_id: str
    pid: int
    runner_uid: int | None = None
    popen: Any | None = None
    pgid: int | None = None


def _target_from_handle(handle) -> KillTarget:
    return KillTarget(
        lane_id=handle.lane_id,
        pid=handle.pid,
        runner_uid=handle.runner_uid,
        popen=handle.popen,
        pgid=getattr(handle, "pgid", None),
    )


def active_targets_from_runlog(records: list[dict[str, Any]]) -> list[KillTarget]:
    active: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        run_id = record.get("run_id")
        lane_id = payload.get("lane_id")
        if not isinstance(run_id, str) or not isinstance(lane_id, str) or not lane_id:
            continue
        pid = payload.get("pid")
        start_time = payload.get("pid_start_time")
        if record.get("event") == "spawn":
            if isinstance(pid, int) and isinstance(start_time, str):
                active[(run_id, lane_id, pid, start_time)] = payload
        elif record.get("event") == "exit":
            for key in list(active):
                key_run_id, key_lane_id, key_pid, _key_start_time = key
                if key_run_id != run_id or key_lane_id != lane_id:
                    continue
                if isinstance(pid, int) and key_pid != pid:
                    continue
                active.pop(key, None)

    targets: list[KillTarget] = []
    for (_run_id, lane_id, _pid, _start_time), payload in active.items():
        pid = payload.get("pid")
        start_time = payload.get("pid_start_time")
        if not isinstance(pid, int) or not isinstance(start_time, str):
            continue
        if not pid_identity_matches(pid, start_time):
            continue
        runner_uid = payload.get("runner_uid")
        pgid = payload.get("pgid")
        targets.append(
            KillTarget(
                lane_id=lane_id,
                pid=pid,
                runner_uid=runner_uid if isinstance(runner_uid, int) else None,
                pgid=pgid if isinstance(pgid, int) else None,
            )
        )
    return targets


def _process_confirmed_dead(pid: int) -> bool:
    return not process_exists(pid) or process_state(pid) == "Z"


def _process_group_confirmed_dead(pgid: int | None) -> bool:
    if pgid is None:
        return True
    proc = "/proc"
    if os.path.isdir(proc):
        for name in os.listdir(proc):
            if not name.isdigit():
                continue
            try:
                stat = os.path.join(proc, name, "stat")
                with open(stat, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                continue
            end = text.rfind(")")
            if end < 0:
                continue
            fields = text[end + 2 :].split()
            if len(fields) < 3:
                continue
            state = fields[0]
            try:
                proc_pgid = int(fields[2])
            except ValueError:
                continue
            if proc_pgid == pgid and state != "Z":
                return False
        return True
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _signal_target(target: KillTarget, sig: int) -> None:
    if target.pgid is not None:
        os.killpg(target.pgid, sig)
    else:
        os.kill(target.pid, sig)


def _target_confirmed_dead(target: KillTarget) -> bool:
    return _process_confirmed_dead(target.pid) and _process_group_confirmed_dead(target.pgid)


def _terminate(target: KillTarget, grace: float) -> tuple[bool, int | None]:
    popen = target.popen
    if popen is None:
        if _target_confirmed_dead(target):
            return True, None
        try:
            _signal_target(target, signal.SIGTERM)
        except ProcessLookupError:
            return True, None
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if _target_confirmed_dead(target):
                return True, -15
            time.sleep(0.02)
        try:
            _signal_target(target, signal.SIGKILL)
        except ProcessLookupError:
            return True, None
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if _target_confirmed_dead(target):
                return True, -9
            time.sleep(0.02)
        return _target_confirmed_dead(target), None

    if popen.poll() is not None and _target_confirmed_dead(target):
        return True, popen.returncode
    try:
        _signal_target(target, signal.SIGTERM)
    except ProcessLookupError:
        return True, None
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if popen.poll() is not None and _target_confirmed_dead(target):
            return True, popen.returncode
        time.sleep(0.02)
    try:
        _signal_target(target, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        popen.wait(timeout=grace)
    except Exception:
        pass
    code = popen.poll()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if code is not None and _target_confirmed_dead(target):
            return True, code
        time.sleep(0.02)
        code = popen.poll()
    return code is not None and _target_confirmed_dead(target), code


def kill_all(
    supervisor,
    log,
    run_id: str,
    grace: float = _TERM_GRACE_SECONDS,
    targets: list[KillTarget] | None = None,
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    kill_targets = (
        [_target_from_handle(handle) for handle in supervisor.handles()]
        if targets is None
        else list(targets)
    )
    if not kill_targets:
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
    for target in kill_targets:
        confirmed, code = _terminate(target, grace)
        outcomes.append(
            {
                "lane_id": target.lane_id,
                "pid": target.pid,
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
                    "lane_id": target.lane_id,
                    "pid": target.pid,
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
