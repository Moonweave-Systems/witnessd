"""W3 Team Ledger artifact builders."""

from __future__ import annotations


def build_evidence_next_verdict(
    *, blocking_reasons: list[str] | None = None
) -> dict[str, object]:
    reasons = list(blocking_reasons or [])
    return {
        "command": "evidence-next",
        "decision": "blocked" if reasons else "continue",
        "blocking_reasons": reasons,
    }


def _self_test() -> None:
    verdict = build_evidence_next_verdict()
    assert verdict["command"] == "evidence-next"
    assert verdict["decision"] == "continue"
