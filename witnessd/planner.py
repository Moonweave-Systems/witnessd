"""W11 Planner/Orchestrator primitives.

The planner layer is deliberately pure: it builds lane packets, seals their
canonical bytes, and derives dispatch records. It does not spawn processes,
touch git, sign evidence, or decide completion.
"""

from __future__ import annotations

import os
import json
from typing import Any

from witnessd.canonical import canonical_hash


SCHEMA_VERSION = "1.0"
SEALED_PLAN_KIND = "witnessd-sealed-plan"
REQUIRED_PACKET_KEYS = {
    "lane_id",
    "adapter",
    "tier",
    "region",
    "prompt",
    "budget",
    "stop_rule",
}
REQUIRED_BUDGET_KEYS = {"max_tokens", "max_usd", "max_depth"}


class PlannerError(ValueError):
    pass


def lane_packet_to_team_lane(packet: dict[str, Any]) -> str:
    normalized = validate_lane_packet(packet)
    prompt = str(normalized["prompt"])
    if ":" in prompt:
        raise PlannerError("ERR_PLAN_PACKET_PROMPT")
    return (
        f"{normalized['lane_id']}:"
        f"adapter={normalized['adapter']}:"
        f"tier={normalized['tier']}:"
        f"region={','.join(normalized['region'])}:"
        f"prompt={prompt}"
    )


def lane_packet_from_team_lane(
    lane: dict[str, Any],
    *,
    budget: dict[str, Any],
    stop_rule: str,
) -> dict[str, Any]:
    packet = {
        "lane_id": lane.get("lane_id"),
        "adapter": lane.get("adapter"),
        "tier": lane.get("tier"),
        "region": lane.get("region"),
        "prompt": lane.get("prompt"),
        "budget": budget,
        "stop_rule": stop_rule,
    }
    return validate_lane_packet(packet)


def validate_lane_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise PlannerError("ERR_PLAN_PACKET_SCHEMA")
    missing = sorted(REQUIRED_PACKET_KEYS - set(packet))
    if missing:
        if "adapter" in missing:
            raise PlannerError("ERR_PLAN_PACKET_ADAPTER")
        raise PlannerError("ERR_PLAN_PACKET_SCHEMA")

    lane_id = str(packet["lane_id"]).strip()
    if not lane_id:
        raise PlannerError("ERR_PLAN_PACKET_LANE_ID")
    adapter = str(packet["adapter"]).strip()
    if not adapter:
        raise PlannerError("ERR_PLAN_PACKET_ADAPTER")
    tier = str(packet["tier"]).strip()
    if not tier:
        raise PlannerError("ERR_PLAN_PACKET_TIER")
    prompt = str(packet["prompt"]).strip()
    if not prompt:
        raise PlannerError("ERR_PLAN_PACKET_PROMPT")
    stop_rule = str(packet["stop_rule"]).strip()
    if not stop_rule:
        raise PlannerError("ERR_PLAN_PACKET_STOP_RULE")

    region = packet["region"]
    if not isinstance(region, list) or not all(
        isinstance(path, str) and path.strip() for path in region
    ):
        raise PlannerError("ERR_PLAN_PACKET_REGION")
    budget = packet["budget"]
    if not isinstance(budget, dict) or set(budget) != REQUIRED_BUDGET_KEYS:
        raise PlannerError("ERR_PLAN_PACKET_BUDGET")

    normalized: dict[str, Any] = {
        "lane_id": lane_id,
        "adapter": adapter,
        "tier": tier,
        "region": [path.strip() for path in region],
        "prompt": prompt,
        "budget": {
            "max_tokens": int(budget["max_tokens"]),
            "max_usd": float(budget["max_usd"]),
            "max_depth": int(budget["max_depth"]),
        },
        "stop_rule": stop_rule,
    }
    if packet.get("merge_lane") is True:
        normalized["merge_lane"] = True
    return normalized


def seal_plan(packets: list[dict[str, Any]], *, goal: str) -> dict[str, Any]:
    normalized = [validate_lane_packet(packet) for packet in packets]
    _assert_region_disjoint_or_explicit_merge(normalized)
    return {
        "kind": SEALED_PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "goal": str(goal),
        "packets": normalized,
        "plan_hash": canonical_hash(normalized),
    }


def plan_heuristic(goal: str, *, seed: str, root: str) -> list[dict[str, Any]]:
    root_fingerprint = _root_fingerprint(root)
    lane_hash = canonical_hash(
        {"goal": str(goal), "seed": str(seed), "root": root_fingerprint}
    )[:12]
    packet = {
        "lane_id": f"plan-{lane_hash}",
        "adapter": "shell",
        "tier": "quick",
        "region": [f"w11/{lane_hash}.txt"],
        "prompt": f"Record planner evidence for goal {goal}",
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "stop_rule": "evidence-pending",
    }
    return [validate_lane_packet(packet)]


def parse_draft_packets(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlannerError("ERR_PLAN_DRAFT_PARSE") from exc
    packets = parsed.get("packets") if isinstance(parsed, dict) else parsed
    if not isinstance(packets, list):
        raise PlannerError("ERR_PLAN_DRAFT_SCHEMA")
    return [validate_lane_packet(packet) for packet in packets]


def dispatch(sealed_plan: dict[str, Any]) -> list[dict[str, Any]]:
    if sealed_plan.get("kind") != SEALED_PLAN_KIND:
        raise PlannerError("ERR_PLAN_SEALED_KIND")
    packets = sealed_plan.get("packets")
    if not isinstance(packets, list):
        raise PlannerError("ERR_PLAN_SEALED_PACKETS")
    normalized = [validate_lane_packet(packet) for packet in packets]
    plan_hash = sealed_plan.get("plan_hash")
    if plan_hash != canonical_hash(normalized):
        raise PlannerError("ERR_PLAN_HASH_MISMATCH")

    events: list[dict[str, Any]] = []
    for index, packet in enumerate(normalized):
        packet_hash = canonical_hash(packet)
        idempotency_key = canonical_hash(
            {
                "index": index,
                "lane_id": packet["lane_id"],
                "packet_hash": packet_hash,
                "plan_hash": plan_hash,
            }
        )
        events.append(
            {
                "kind": "witnessd-dispatch-event",
                "schema_version": SCHEMA_VERSION,
                "plan_hash": plan_hash,
                "lane_id": packet["lane_id"],
                "packet_hash": packet_hash,
                "idempotency_key": idempotency_key,
            }
        )
    return events


def _root_fingerprint(root: str) -> list[str]:
    try:
        return sorted(name for name in os.listdir(root) if not name.startswith("."))
    except FileNotFoundError as exc:
        raise PlannerError("ERR_PLAN_ROOT_MISSING") from exc


def _assert_region_disjoint_or_explicit_merge(packets: list[dict[str, Any]]) -> None:
    if any(packet.get("merge_lane") is True for packet in packets):
        return
    seen: set[str] = set()
    for packet in packets:
        for path in packet["region"]:
            if path in seen:
                raise PlannerError("ERR_PLAN_REGION_OVERLAP")
            seen.add(path)
