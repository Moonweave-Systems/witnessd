#!/usr/bin/env python3
"""Print a W4 fixture demo without claiming verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "w4"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    receipt = _json(FIXTURES / "runner-receipt-codex.json")
    state = _json(FIXTURES / "state-isolation" / "snapshot.json")
    route_events = _jsonl(FIXTURES / "route-degrade.jsonl")
    budget_events = _jsonl(FIXTURES / "budget-blowout.jsonl")

    route_names = [event.get("event") for event in route_events]
    budget_names = [event.get("event") for event in budget_events]
    if receipt.get("runner_kind") != "codex-cli":
        return 1
    if state.get("mock_store_unchanged") is not True:
        return 1
    if "route_blocked" not in route_names:
        return 1
    if "budget_exceeded" not in budget_names or "spawn" in budget_names:
        return 1

    print("adapter: evidence-pending runner_kind=codex-cli")
    print("state: evidence-pending mock_store_unchanged=true")
    print("route: blocked reason=model_not_supported_exhausted")
    print("budget: blocked reason=budget_exceeded spawn=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
