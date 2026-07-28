"""Internal claim axes and construction invariants.

Public artifacts keep their established schemas.  This module separates the
dimensions those schemas currently compress into legacy status labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ClaimSource(str, Enum):
    OPERATOR = "operator"
    PRODUCER = "producer"
    OBSERVER = "observer"
    VERIFIER = "verifier"


class ClaimObservation(str, Enum):
    REQUESTED = "requested"
    OBSERVED = "observed"
    MISSING = "missing"


class ClaimIntegrity(str, Enum):
    UNBOUND = "unbound"
    SEALED = "sealed"
    BUNDLE_BOUND = "bundle-bound"
    SIGNATURE_CHECKED = "signature-checked"


class ClaimEvaluation(str, Enum):
    UNEVALUATED = "unevaluated"
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    REFUTED = "refuted"


class ClaimEffect(str, Enum):
    ADVISORY = "advisory"
    BLOCKING = "blocking"


class ClaimFreshness(str, Enum):
    PENDING = "pending"
    CURRENT = "current"
    STALE = "stale"


_AXIS_TYPES = (
    ("source", ClaimSource),
    ("observation", ClaimObservation),
    ("integrity", ClaimIntegrity),
    ("evaluation", ClaimEvaluation),
    ("effect", ClaimEffect),
    ("freshness", ClaimFreshness),
)


@dataclass(frozen=True)
class Claim:
    value: Any
    source: ClaimSource
    observation: ClaimObservation
    integrity: ClaimIntegrity
    evaluation: ClaimEvaluation
    effect: ClaimEffect
    freshness: ClaimFreshness

    def __post_init__(self) -> None:
        for field, axis_type in _AXIS_TYPES:
            if not isinstance(getattr(self, field), axis_type):
                raise TypeError(f"{field} must be {axis_type.__name__}")
        if (
            self.evaluation is not ClaimEvaluation.UNEVALUATED
            and self.source is not ClaimSource.VERIFIER
        ):
            raise ValueError("only verifier claims may carry an evaluation")

    @classmethod
    def from_producer(
        cls,
        *,
        value: Any,
        observation: ClaimObservation,
        integrity: ClaimIntegrity,
        effect: ClaimEffect,
        freshness: ClaimFreshness,
    ) -> Claim:
        return cls(
            value=value,
            source=ClaimSource.PRODUCER,
            observation=observation,
            integrity=integrity,
            evaluation=ClaimEvaluation.UNEVALUATED,
            effect=effect,
            freshness=freshness,
        )

    @classmethod
    def from_verifier(
        cls,
        *,
        value: Any,
        observation: ClaimObservation,
        integrity: ClaimIntegrity,
        evaluation: ClaimEvaluation,
        effect: ClaimEffect,
        freshness: ClaimFreshness,
    ) -> Claim:
        if evaluation is ClaimEvaluation.UNEVALUATED:
            raise ValueError("verifier result must be evaluated")
        return cls(
            value=value,
            source=ClaimSource.VERIFIER,
            observation=observation,
            integrity=integrity,
            evaluation=evaluation,
            effect=effect,
            freshness=freshness,
        )
