from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from witnessd.canonical import canonical_hash  # noqa: E402
from witnessd.planner import (  # noqa: E402
    PlannerError,
    dispatch,
    plan_heuristic,
    seal_plan,
    validate_lane_packet,
)


FIXTURE_DIR = ROOT / "fixtures" / "w11"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_disjoint(packets: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for packet in packets:
        for path in packet["region"]:
            if path in seen:
                raise AssertionError(f"region overlap: {path}")
            seen.add(path)


def _assert_negative_overlap_rejected() -> None:
    packet = {
        "lane_id": "overlap-a",
        "adapter": "shell",
        "tier": "quick",
        "region": ["shared.txt"],
        "prompt": "write shared",
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "stop_rule": "evidence-pending",
    }
    other = dict(packet)
    other["lane_id"] = "overlap-b"
    try:
        seal_plan([packet, other], goal="negative overlap")
    except PlannerError as exc:
        if str(exc) == "ERR_PLAN_REGION_OVERLAP":
            return
        raise AssertionError(f"wrong overlap error: {exc}") from exc
    raise AssertionError("overlap plan was accepted")


def main() -> int:
    sealed = _load_json(FIXTURE_DIR / "sealed-plan.json")
    dispatch_log = _load_jsonl(FIXTURE_DIR / "dispatch-log.jsonl")
    packets = sealed["packets"]

    for packet in packets:
        validate_lane_packet(packet)
    recomputed_hash = canonical_hash(packets)
    if sealed["plan_hash"] != recomputed_hash:
        raise AssertionError("plan_hash mismatch")
    if seal_plan(packets, goal=sealed["goal"]) != sealed:
        raise AssertionError("seal_plan did not reproduce fixture")
    _assert_disjoint(packets)

    events_a = dispatch(sealed)
    events_b = dispatch(sealed)
    if events_a != events_b:
        raise AssertionError("dispatch is not deterministic")
    if dispatch_log != events_a:
        raise AssertionError("dispatch log mismatch")

    heuristic_a = plan_heuristic(
        sealed["goal"], seed="w11-fixture", root=str(ROOT)
    )
    heuristic_b = plan_heuristic(
        sealed["goal"], seed="w11-fixture", root=str(ROOT)
    )
    heuristic_hash_a = canonical_hash(heuristic_a)
    heuristic_hash_b = canonical_hash(heuristic_b)
    if heuristic_hash_a != heuristic_hash_b:
        raise AssertionError("heuristic planner is not deterministic")

    _assert_negative_overlap_rejected()

    print(f"w11 plan_hash: {recomputed_hash}")
    print(f"w11 dispatch_events: {len(events_a)}")
    print(f"w11 heuristic_hash_a: {heuristic_hash_a}")
    print(f"w11 heuristic_hash_b: {heuristic_hash_b}")
    print("revalidate_w11: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
