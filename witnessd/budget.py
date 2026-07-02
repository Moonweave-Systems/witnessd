"""W4 hard-stop cost circuit breaker."""

from __future__ import annotations

from typing import Any

from witnessd.runlog import append_runlog


class BudgetExceededError(RuntimeError):
    def __init__(self, *, metric: str, limit: float, observed: float) -> None:
        super().__init__(
            f"ERR_WITNESSD_BUDGET_EXCEEDED: {metric} {observed} exceeds {limit}"
        )
        self.code = "ERR_WITNESSD_BUDGET_EXCEEDED"
        self.metric = metric
        self.limit = limit
        self.observed = observed


class CostBreaker:
    def __init__(
        self,
        *,
        log: Any,
        max_tokens: int,
        max_usd: float,
        max_depth: int,
    ) -> None:
        self.log = log
        self.max_tokens = max_tokens
        self.max_usd = max_usd
        self.max_depth = max_depth
        self.spent_tokens = 0
        self.spent_usd = 0.0

    def check_can_spawn(
        self,
        *,
        task_id: str,
        predicted_tokens: int,
        predicted_usd: float,
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._exceeded(
                task_id=task_id,
                metric="depth",
                limit=float(self.max_depth),
                observed=float(depth),
            )
        predicted_total_tokens = self.spent_tokens + predicted_tokens
        if predicted_total_tokens > self.max_tokens:
            self._exceeded(
                task_id=task_id,
                metric="tokens",
                limit=float(self.max_tokens),
                observed=float(predicted_total_tokens),
            )
        predicted_total_usd = self.spent_usd + predicted_usd
        if predicted_total_usd > self.max_usd:
            self._exceeded(
                task_id=task_id,
                metric="usd",
                limit=float(self.max_usd),
                observed=float(predicted_total_usd),
            )

    def charge(self, *, task_id: str, tokens: int, usd: float) -> None:
        self.spent_tokens += tokens
        self.spent_usd += usd
        append_runlog(
            self.log,
            run_id=task_id,
            event="spend_measured",
            payload={"tokens": tokens, "usd": usd},
        )

    def _exceeded(
        self,
        *,
        task_id: str,
        metric: str,
        limit: float,
        observed: float,
    ) -> None:
        append_runlog(
            self.log,
            run_id=task_id,
            event="budget_exceeded",
            error_code="ERR_WITNESSD_BUDGET_EXCEEDED",
            payload={"metric": metric, "limit": limit, "observed": observed},
        )
        raise BudgetExceededError(metric=metric, limit=limit, observed=observed)


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog

    with tempfile.TemporaryDirectory() as directory:
        breaker = CostBreaker(
            log=EventLog(os.path.join(directory, "runlog.jsonl")),
            max_tokens=1,
            max_usd=1.0,
            max_depth=1,
        )
        try:
            breaker.check_can_spawn(
                task_id="self-test",
                predicted_tokens=2,
                predicted_usd=0.0,
                depth=1,
            )
        except BudgetExceededError as exc:
            assert exc.metric == "tokens"
        else:
            raise AssertionError("budget overflow must fail closed")
