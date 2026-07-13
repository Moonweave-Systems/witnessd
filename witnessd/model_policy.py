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
        },
        {
            "role_kind": "runner",
            "tier": "agentic",
            "candidates": [{"adapter": "codex", "model": "gpt-5.6-sol"}],
        },
        {
            "role_kind": "runner",
            "tier": "frontier",
            "candidates": [{"adapter": "codex", "model": "gpt-5.6-sol"}],
        },
        {
            "role_kind": "reviewer",
            "tier": "quick",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
        },
        {
            "role_kind": "reviewer",
            "tier": "agentic",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
        },
        {
            "role_kind": "reviewer",
            "tier": "frontier",
            "candidates": [{"adapter": "agy", "model": "gemini-3.5-flash"}],
        },
    ],
}


def resolve_policy_route(
    policy: dict[str, Any], *, role_kind: str, tier: str
) -> dict[str, str] | None:
    for route in policy.get("routes", []):
        if route.get("role_kind") == role_kind and route.get("tier") == tier:
            candidates = route.get("candidates") or []
            if not candidates:
                return None
            first = candidates[0]
            return {"adapter": str(first["adapter"]), "model": str(first["model"])}
    return None
