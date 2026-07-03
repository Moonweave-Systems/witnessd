"""W11 Planner/Orchestrator primitives.

The planner layer is deliberately pure: it builds lane packets, seals their
canonical bytes, and derives dispatch records. It does not spawn processes,
touch git, sign evidence, or decide completion.
"""

from __future__ import annotations

import json
import os
import posixpath
from typing import Any

from witnessd.adapters.base import RUNNER_KIND_BY_ADAPTER
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
    if adapter not in _valid_adapters():
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

    try:
        region = _normalize_region(packet["region"])
    except (TypeError, ValueError) as exc:
        raise PlannerError("ERR_PLAN_PACKET_REGION") from exc
    if not region:
        raise PlannerError("ERR_PLAN_PACKET_REGION")
    budget = packet["budget"]
    if not isinstance(budget, dict) or set(budget) != REQUIRED_BUDGET_KEYS:
        raise PlannerError("ERR_PLAN_PACKET_BUDGET")

    normalized: dict[str, Any] = {
        "lane_id": lane_id,
        "adapter": adapter,
        "tier": tier,
        "region": region,
        "prompt": prompt,
        "budget": {
            "max_tokens": int(budget["max_tokens"]),
            "max_usd": float(budget["max_usd"]),
            "max_depth": int(budget["max_depth"]),
        },
        "stop_rule": stop_rule,
    }
    return normalized


def _valid_adapters() -> set[str]:
    return set(RUNNER_KIND_BY_ADAPTER) | {"shell"}


def _normalize_region(raw_region: Any) -> list[str]:
    if not isinstance(raw_region, list):
        raise TypeError("region must be a list")
    normalized: set[str] = set()
    for raw_path in raw_region:
        if not isinstance(raw_path, str):
            raise TypeError("region path must be a string")
        path = posixpath.normpath(raw_path.replace("\\", "/").strip())
        if (
            path in ("", ".")
            or path.startswith("/")
            or path.startswith("../")
            or path == ".."
        ):
            raise ValueError("region path escapes repository")
        normalized.add(path)
    return sorted(normalized)


def seal_plan(
    packets: list[dict[str, Any]],
    *,
    goal: str,
    merge_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = [validate_lane_packet(packet) for packet in packets]
    normalized_merge_groups = _normalize_merge_groups(normalized, merge_groups)
    if normalized_merge_groups:
        _assert_region_overlaps_covered(normalized, normalized_merge_groups)
    else:
        _assert_region_disjoint(normalized)
    sealed = {
        "kind": SEALED_PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "goal": str(goal),
        "packets": normalized,
    }
    if normalized_merge_groups:
        sealed["merge_groups"] = normalized_merge_groups
        sealed["plan_hash"] = canonical_hash(
            {"packets": normalized, "merge_groups": normalized_merge_groups}
        )
    else:
        sealed["plan_hash"] = canonical_hash(normalized)
    return sealed


def plan_heuristic(
    goal: str,
    *,
    seed: str,
    root: str,
    adapter: str = "shell",
    budget: dict[str, Any] | None = None,
    prompt: str | None = None,
    tier: str | None = None,
) -> list[dict[str, Any]]:
    root_fingerprint = _root_fingerprint(root)
    lane_hash = canonical_hash(
        {"goal": str(goal), "seed": str(seed), "root": root_fingerprint}
    )[:12]
    if budget is None:
        budget = {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1}
    if prompt is None:
        prompt = (
            f"Record planner evidence for goal {goal}"
            if adapter == "shell"
            else str(goal)
        )
    packet = {
        "lane_id": f"plan-{lane_hash}",
        "adapter": adapter,
        "tier": tier or ("quick" if adapter == "shell" else "agentic"),
        "region": [f"w11/{lane_hash}.txt"],
        "prompt": prompt,
        "budget": budget,
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
    merge_groups = merge_groups_from_overlapping_regions(sealed_plan)
    expected_hash = (
        canonical_hash({"packets": normalized, "merge_groups": merge_groups})
        if merge_groups
        else canonical_hash(normalized)
    )
    if plan_hash != expected_hash:
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


def _assert_region_disjoint(packets: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for packet in packets:
        for path in packet["region"]:
            if path in seen:
                raise PlannerError("ERR_PLAN_REGION_OVERLAP")
            seen.add(path)


def merge_groups_from_overlapping_regions(
    sealed_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    packets = sealed_plan.get("packets")
    if not isinstance(packets, list):
        raise PlannerError("ERR_PLAN_SEALED_PACKETS")
    normalized = [validate_lane_packet(packet) for packet in packets]
    return _normalize_merge_groups(normalized, sealed_plan.get("merge_groups"))


def _normalize_merge_groups(
    packets: list[dict[str, Any]],
    merge_groups: Any,
) -> list[dict[str, Any]]:
    if merge_groups is None:
        return []
    if not isinstance(merge_groups, list):
        raise PlannerError("ERR_PLAN_MERGE_GROUP_SCHEMA")
    packet_ids = {packet["lane_id"] for packet in packets}
    normalized: list[dict[str, Any]] = []
    seen_merge_lanes: set[str] = set()
    for raw in merge_groups:
        if not isinstance(raw, dict):
            raise PlannerError("ERR_PLAN_MERGE_GROUP_SCHEMA")
        lane_id = str(raw.get("lane_id", "")).strip()
        if not lane_id or lane_id not in packet_ids or lane_id in seen_merge_lanes:
            raise PlannerError("ERR_PLAN_MERGE_GROUP_LANE")
        seen_merge_lanes.add(lane_id)
        sources = raw.get("sources")
        if (
            not isinstance(sources, list)
            or len(sources) < 2
            or any(not isinstance(source, str) or not source.strip() for source in sources)
        ):
            raise PlannerError("ERR_PLAN_MERGE_GROUP_SOURCES")
        normalized_sources = sorted({source.strip() for source in sources})
        if lane_id in normalized_sources or any(source not in packet_ids for source in normalized_sources):
            raise PlannerError("ERR_PLAN_MERGE_GROUP_SOURCES")
        try:
            files = _normalize_region(raw.get("files"))
        except (TypeError, ValueError) as exc:
            raise PlannerError("ERR_PLAN_MERGE_GROUP_FILES") from exc
        if not files:
            raise PlannerError("ERR_PLAN_MERGE_GROUP_FILES")
        normalized.append(
            {"lane_id": lane_id, "sources": normalized_sources, "files": files}
        )
    return sorted(normalized, key=lambda group: group["lane_id"])


def _assert_region_overlaps_covered(
    packets: list[dict[str, Any]],
    merge_groups: list[dict[str, Any]],
) -> None:
    packets_by_id = {packet["lane_id"]: packet for packet in packets}
    merge_lane_ids = {group["lane_id"] for group in merge_groups}
    owners_by_path: dict[str, list[str]] = {}
    for packet in packets:
        if packet["lane_id"] in merge_lane_ids:
            continue
        for path in packet["region"]:
            owners_by_path.setdefault(path, []).append(packet["lane_id"])

    covered: set[tuple[str, tuple[str, ...]]] = set()
    for group in merge_groups:
        source_key = tuple(group["sources"])
        merge_region = set(packets_by_id[group["lane_id"]]["region"])
        if merge_region & set(group["files"]):
            raise PlannerError("ERR_PLAN_REGION_OVERLAP")
        for path in group["files"]:
            covered.add((path, source_key))

    for path, owners in owners_by_path.items():
        unique_owners = sorted(set(owners))
        if len(unique_owners) <= 1:
            continue
        if (path, tuple(unique_owners)) not in covered:
            raise PlannerError("ERR_PLAN_REGION_OVERLAP")
