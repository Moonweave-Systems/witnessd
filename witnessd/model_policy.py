"""Model routing policy: (role kind, tier) -> (adapter, model).

This is deliberately a thin, pure layer on top of the model-declaration work
(model_declaration.py): the policy only decides *what* adapter/model a lane
should ask for, it does not decide whether the request was honored -- that
verification-status judgment stays entirely in model_declaration.py and the
per-adapter fail-closed rejection checks.

v0 is a static, deterministic lookup table: exact (role_kind, tier) match,
first candidate only. `candidates` is a list (not a single value) so a future
version can add live-capability-based selection or an operator override file
without a schema change, but nothing in this version ever walks past index 0
or falls back silently -- an unmapped (role_kind, tier) combo is the caller's
problem to fail closed on, not something to paper over here.
"""

from __future__ import annotations

from typing import Any

MODEL_POLICY_KIND = "moonweave-model-policy"
MODEL_POLICY_SCHEMA_VERSION = "1.0"

DEFAULT_MODEL_POLICY: dict[str, Any] = {
    "kind": MODEL_POLICY_KIND,
    "schema_version": MODEL_POLICY_SCHEMA_VERSION,
    "routes": [
        {
            "role_kind": "runner",
            "tier": "quick",
            "candidates": [{"adapter": "codex", "model": "gpt-5.6-luna"}],
            "budget": {"max_tokens": 200000, "max_usd": 1.0, "max_depth": 1},
        },
        {
            "role_kind": "runner",
            "tier": "agentic",
            "candidates": [{"adapter": "codex", "model": "gpt-5.6-sol"}],
            "budget": {"max_tokens": 600000, "max_usd": 3.0, "max_depth": 1},
        },
        {
            "role_kind": "runner",
            "tier": "frontier",
            "candidates": [{"adapter": "codex", "model": "gpt-5.6-sol"}],
            "budget": {"max_tokens": 1000000, "max_usd": 6.0, "max_depth": 1},
        },
        {
            "role_kind": "reviewer",
            "tier": "quick",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
            "budget": {"max_tokens": 100000, "max_usd": 0.5, "max_depth": 1},
        },
        {
            "role_kind": "reviewer",
            "tier": "agentic",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
            "budget": {"max_tokens": 300000, "max_usd": 1.5, "max_depth": 1},
        },
        {
            "role_kind": "reviewer",
            "tier": "frontier",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
            "budget": {"max_tokens": 500000, "max_usd": 2.5, "max_depth": 1},
        },
    ],
}


def resolve_policy_route(
    policy: dict[str, Any],
    *,
    role_kind: str,
    tier: str,
    caller_budget: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for route in policy.get("routes", []):
        if route.get("role_kind") == role_kind and route.get("tier") == tier:
            candidates = route.get("candidates") or []
            if not candidates:
                return None
            first = candidates[0]
            return {
                "adapter": str(first["adapter"]),
                "model": str(first["model"]),
                "budget": compose_route_budget(route.get("budget"), caller_budget),
            }
    return None


def compose_route_budget(
    route_budget: dict[str, Any] | None,
    caller_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(route_budget, dict):
        raise ValueError("ERR_MODEL_POLICY_BUDGET_REQUIRED")
    budget = {
        "max_tokens": int(route_budget["max_tokens"]),
        "max_usd": float(route_budget["max_usd"]),
        "max_depth": int(route_budget["max_depth"]),
    }
    if caller_budget is None:
        return budget
    return {
        "max_tokens": min(budget["max_tokens"], int(caller_budget["max_tokens"])),
        "max_usd": min(budget["max_usd"], float(caller_budget["max_usd"])),
        "max_depth": min(budget["max_depth"], int(caller_budget["max_depth"])),
    }
