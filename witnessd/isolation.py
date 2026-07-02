"""Per-spawn isolation probe wrapper around Depone's verifier contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from depone.agent_fabric.isolation import (
    ISOLATION_MODEL,
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
    _self_test as _depone_isolation_self_test,
    probe_isolation_facts,
)


def probe_lane_isolation(
    *,
    observer_dir: str,
    runner_uid: int | None,
    model: str = ISOLATION_MODEL,
    observer_launched: bool = False,
) -> dict[str, Any]:
    return probe_isolation_facts(
        Path(observer_dir),
        runner_uid=runner_uid,
        model=model,
        observer_launched=observer_launched,
    )


def isolation_self_test() -> None:
    _depone_isolation_self_test()


def _self_test() -> None:
    isolation_self_test()


__all__ = [
    "ISOLATION_MODEL",
    "UID_OBSERVER_LAUNCHED_ISOLATION_MODEL",
    "isolation_self_test",
    "probe_lane_isolation",
]
