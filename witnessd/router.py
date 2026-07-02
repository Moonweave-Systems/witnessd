"""W4 model routing with explicit retry/degrade/blocked events."""

from __future__ import annotations

from typing import Callable, Any

from witnessd.runlog import append_runlog

TIER_CANDIDATES = {
    "quick": ["gpt-5.3-codex-spark", "gpt-5.4-mini", "gpt-5.5"],
    "agentic": ["gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark"],
    "frontier": ["gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark"],
}


class RouteExhaustedError(RuntimeError):
    def __init__(self, message: str = "route candidates exhausted") -> None:
        super().__init__(f"ERR_WITNESSD_ROUTE_EXHAUSTED: {message}")
        self.code = "ERR_WITNESSD_ROUTE_EXHAUSTED"


def route_model(
    *,
    task_id: str,
    tier: str,
    log: Any,
    is_supported: Callable[[str], bool],
    concurrency_key: str | None = None,
) -> dict[str, Any]:
    if tier not in TIER_CANDIDATES:
        raise ValueError(f"unknown model tier: {tier}")

    attempts: list[dict[str, Any]] = []
    candidates = TIER_CANDIDATES[tier]
    for index, model in enumerate(candidates):
        supported = bool(is_supported(model))
        attempts.append({"model": model, "supported": supported})
        if not supported:
            append_runlog(
                log,
                run_id=task_id,
                event="model_not_supported",
                payload={"tier": tier, "model": model},
            )
            continue

        decision = {
            "model": model,
            "tier": tier,
            "concurrency_key": concurrency_key or f"{task_id}:{tier}",
            "degraded": index != 0,
            "attempts": attempts,
        }
        append_runlog(
            log,
            run_id=task_id,
            event="route_selected",
            payload={
                "tier": tier,
                "model": model,
                "degraded": decision["degraded"],
                "concurrency_key": decision["concurrency_key"],
            },
        )
        return decision

    append_runlog(
        log,
        run_id=task_id,
        event="route_blocked",
        error_code="ERR_WITNESSD_ROUTE_EXHAUSTED",
        payload={
            "tier": tier,
            "reason": "model_not_supported_exhausted",
            "attempts": attempts,
        },
    )
    raise RouteExhaustedError()


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog

    with tempfile.TemporaryDirectory() as directory:
        log = EventLog(os.path.join(directory, "runlog.jsonl"))
        decision = route_model(
            task_id="self-test",
            tier="quick",
            log=log,
            is_supported=lambda model: model == TIER_CANDIDATES["quick"][-1],
        )
        assert decision["degraded"] is True
        try:
            route_model(
                task_id="self-test-blocked",
                tier="quick",
                log=log,
                is_supported=lambda _model: False,
            )
        except RouteExhaustedError:
            pass
        else:
            raise AssertionError("route exhaustion must fail closed")
