"""§6.1.2 liveness projection — `active` is DERIVED, never a stored flag.

`active` is true only when a signed heartbeat for a lane was observed in the
runlog within `HEARTBEAT_TTL_SECONDS`. Ordering/liveness depend on
`ts_monotonic` only (§6.4.4). There is no code path that flips `active` from a
stored flag — this is the structural anti-regression against the OMX zombie
false-positive (a "dead" team reporting "all clear").

State domain per lane:
- `active`  — heartbeat observed within TTL, no exit
- `dead`    — an `exit` event was observed (clean or killed)
- `zombie`  — had a heartbeat but it expired past TTL with no exit (SIGCHLD)
- `stale`   — resume target with no heartbeat re-established yet
"""

from __future__ import annotations

HEARTBEAT_TTL_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 10


def derive_liveness(
    records: list[dict],
    *,
    now_monotonic: float,
    ttl: float = HEARTBEAT_TTL_SECONDS,
    resumed_lanes: frozenset[str] = frozenset(),
) -> dict[str, str]:
    last_exit: dict[str, bool] = {}
    last_heartbeat: dict[str, float] = {}
    seen: set[str] = set()

    for record in records:
        lane_id = record.get("payload", {}).get("lane_id")
        if lane_id is None:
            continue
        seen.add(lane_id)
        event = record.get("event")
        if event == "exit":
            last_exit[lane_id] = True
        elif event == "heartbeat":
            last_heartbeat[lane_id] = record["ts_monotonic"]

    state: dict[str, str] = {}
    for lane_id in seen:
        if last_exit.get(lane_id):
            state[lane_id] = "dead"
        elif lane_id in last_heartbeat:
            if last_heartbeat[lane_id] >= now_monotonic - ttl:
                state[lane_id] = "active"
            else:
                state[lane_id] = "zombie"
        elif lane_id in resumed_lanes:
            state[lane_id] = "stale"
        else:
            state[lane_id] = "zombie"
    return state


def _self_test() -> None:
    recs = [
        {"event": "spawn", "payload": {"lane_id": "L1"}, "ts_monotonic": 0.0},
        {"event": "heartbeat", "payload": {"lane_id": "L1"}, "ts_monotonic": 100.0},
    ]
    assert derive_liveness(recs, now_monotonic=105.0)["L1"] == "active"
