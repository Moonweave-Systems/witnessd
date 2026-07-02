#!/usr/bin/env python3
"""Re-derive W4 adapter/routing/budget fixture verdicts from committed bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from depone.agent_fabric.evidence_substrate import (
    ingest_signed_evidence_bundle,
    validate_external_otel_spans,
)
from depone.agent_fabric.paired_run import VALID_RUNNERS, validate_runner_receipt


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "w4"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_runner_receipts() -> None:
    codex = _load_json(FIXTURES / "runner-receipt-codex.json")
    errors = validate_runner_receipt(codex)
    assert errors == [], errors
    assert codex["runner_kind"] == "codex-cli"
    assert codex["runner_kind"] in VALID_RUNNERS

    for name in (
        "runner-receipt-claude-manual.json",
        "runner-receipt-opencode-manual.json",
    ):
        receipt = _load_json(FIXTURES / name)
        errors = validate_runner_receipt(receipt)
        assert errors == [], errors
        assert receipt["runner_kind"] == "manual"
        assert receipt["runner_kind"] in VALID_RUNNERS


def _assert_route_and_budget_events() -> None:
    route_events = _load_jsonl(FIXTURES / "route-degrade.jsonl")
    route_names = [event["event"] for event in route_events]
    assert "model_not_supported" in route_names
    assert "route_blocked" in route_names
    assert "VERIFIED" not in json.dumps(route_events)
    assert "COMPLETE" not in json.dumps(route_events)

    budget_events = _load_jsonl(FIXTURES / "budget-blowout.jsonl")
    budget_names = [event["event"] for event in budget_events]
    assert "budget_exceeded" in budget_names
    assert "spawn" not in budget_names


def _assert_bundle() -> None:
    bundle = _load_json(FIXTURES / "bundle-codex.json")
    spans = bundle["otel_spans"]
    errors = validate_external_otel_spans(spans)
    assert errors == [], errors
    for span in spans:
        for key in span.get("attributes", {}):
            assert not key.startswith("gen_ai.usage."), key

    verdict = ingest_signed_evidence_bundle(
        bundle,
        str(FIXTURES / "keys" / "operator.pub"),
        {
            "capture-manifest": str(FIXTURES / "capture-manifest-codex.json"),
            "observer-capture": str(FIXTURES / "observer-capture-codex.json"),
            "runner-receipt": str(FIXTURES / "runner-receipt-codex.json"),
        },
        otel_spans=spans,
    )
    assert verdict["decision"] == "pass", verdict
    assert verdict["signature_verified"] is True, verdict
    assert verdict["otel_errors"] == [], verdict


def _assert_state_isolation() -> None:
    snapshot = _load_json(FIXTURES / "state-isolation" / "snapshot.json")
    assert snapshot["mock_store_unchanged"] is True
    assert snapshot["mock_store_before"] == snapshot["mock_store_after"]
    assert ".witnessd" in snapshot["codex_home"]
    assert snapshot["codex_home"].startswith("state-isolation/witnessd-root/")


def main() -> int:
    _assert_runner_receipts()
    _assert_route_and_budget_events()
    _assert_bundle()
    _assert_state_isolation()
    print("W4 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
