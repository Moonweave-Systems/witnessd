"""W4 model routing with explicit retry/degrade/blocked events."""

from __future__ import annotations

from typing import Callable, Any

from witnessd.model_policy import DEFAULT_MODEL_POLICY
from witnessd.runlog import append_runlog


class RouteExhaustedError(RuntimeError):
    def __init__(self, message: str = "route candidates exhausted") -> None:
        super().__init__(f"ERR_WITNESSD_ROUTE_EXHAUSTED: {message}")
        self.code = "ERR_WITNESSD_ROUTE_EXHAUSTED"


def _tier_candidates(tier: str) -> list[str]:
    # route_model is role-agnostic, so its tier ladder follows model-policy
    # route order across role kinds (runner first, then reviewer in the default
    # policy) and tries every model declared for that tier exactly once.
    candidates: list[str] = []
    for route in DEFAULT_MODEL_POLICY.get("routes", []):
        if route.get("tier") != tier:
            continue
        for candidate in route.get("candidates") or []:
            model = str(candidate["model"])
            if model not in candidates:
                candidates.append(model)
    if not candidates:
        raise ValueError(f"unknown model tier: {tier}")
    return candidates


def route_model(
    *,
    task_id: str,
    tier: str,
    log: Any,
    is_supported: Callable[[str], bool],
    concurrency_key: str | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    candidates = _tier_candidates(tier)
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
            is_supported=lambda model: model == _tier_candidates("quick")[-1],
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
