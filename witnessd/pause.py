"""W5 hard-pause boundary derived only from the append-only runlog."""

from __future__ import annotations

from typing import Any

from witnessd.runlog import append_runlog

PAUSE_EVENT = "user_pause"
RESUME_EVENT = "user_resume"
ERR_WITNESSD_PAUSED = "ERR_WITNESSD_PAUSED"
ERR_WITNESSD_PAUSE_SOURCE_INVALID = "ERR_WITNESSD_PAUSE_SOURCE_INVALID"
ERR_WITNESSD_RESUME_UNCONFIRMED = "ERR_WITNESSD_RESUME_UNCONFIRMED"

_VALID_SOURCES = frozenset({"signal", "cli"})


class PauseError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def derive_pause_state(records: list[dict[str, Any]]) -> bool:
    paused = False
    for record in records:
        event = record.get("event")
        if event == PAUSE_EVENT:
            paused = True
        elif event == RESUME_EVENT:
            paused = False
    return paused


def append_user_pause(log, run_id: str, source: str) -> dict[str, Any]:
    if source not in _VALID_SOURCES:
        raise PauseError(ERR_WITNESSD_PAUSE_SOURCE_INVALID)
    return append_runlog(log, run_id, PAUSE_EVENT, payload={"source": source})


def append_user_resume(log, run_id: str, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise PauseError(ERR_WITNESSD_RESUME_UNCONFIRMED)
    return append_runlog(log, run_id, RESUME_EVENT, payload={"confirm": True})


def assert_not_paused(records: list[dict[str, Any]]) -> None:
    if derive_pause_state(records):
        raise PauseError(ERR_WITNESSD_PAUSED)


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog

    with tempfile.TemporaryDirectory() as tmp:
        log = EventLog(os.path.join(tmp, "runlog.jsonl"))
        append_user_pause(log, "R", "cli")
        try:
            assert_not_paused(log.read())
        except PauseError as exc:
            assert exc.code == ERR_WITNESSD_PAUSED
        else:
            raise AssertionError("paused runlog must block continuation")
        append_user_resume(log, "R", True)
        assert derive_pause_state(log.read()) is False
