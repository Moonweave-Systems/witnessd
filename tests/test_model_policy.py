from __future__ import annotations

import unittest

from witnessd.model_policy import (
    DEFAULT_MODEL_POLICY,
    MODEL_POLICY_KIND,
    MODEL_POLICY_SCHEMA_VERSION,
    resolve_policy_route,
)


class ModelPolicyTests(unittest.TestCase):
    def test_default_policy_shape(self) -> None:
        self.assertEqual(DEFAULT_MODEL_POLICY["kind"], MODEL_POLICY_KIND)
        self.assertEqual(
            DEFAULT_MODEL_POLICY["schema_version"], MODEL_POLICY_SCHEMA_VERSION
        )
        self.assertIsInstance(DEFAULT_MODEL_POLICY["routes"], list)

    def test_resolves_exact_builtin_route_matrix(self) -> None:
        expected = {
            ("runner", "quick"): ("codex", "gpt-5.6-luna"),
            ("runner", "agentic"): ("codex", "gpt-5.6-sol"),
            ("runner", "frontier"): ("codex", "gpt-5.6-sol"),
            ("reviewer", "quick"): ("agy", "gemini-3.5-flash"),
            ("reviewer", "agentic"): ("agy", "gemini-3.5-flash"),
            ("reviewer", "frontier"): ("agy", "gemini-3.5-flash"),
        }
        actual = {
            (role_kind, tier): (
                route["adapter"],
                route["model"],
            )
            for role_kind in ("runner", "reviewer")
            for tier in ("quick", "agentic", "frontier")
            for route in [
                resolve_policy_route(
                    DEFAULT_MODEL_POLICY, role_kind=role_kind, tier=tier
                )
            ]
        }
        self.assertEqual(actual, expected)

    def test_resolves_reviewer_quick_to_agy(self) -> None:
        route = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="reviewer", tier="quick"
        )
        self.assertIsNotNone(route)
        self.assertEqual(route["adapter"], "agy")
        self.assertEqual(route["model"], "gemini-3.5-flash")

    def test_resolves_tier_budget_and_caps_caller_budget(self) -> None:
        quick = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="runner", tier="quick"
        )
        agentic = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="runner", tier="agentic"
        )
        frontier = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="runner", tier="frontier"
        )
        self.assertIsNotNone(quick)
        self.assertIsNotNone(agentic)
        self.assertIsNotNone(frontier)
        self.assertLess(quick["budget"]["max_tokens"], agentic["budget"]["max_tokens"])
        self.assertLess(
            agentic["budget"]["max_tokens"], frontier["budget"]["max_tokens"]
        )
        self.assertLess(quick["budget"]["max_usd"], agentic["budget"]["max_usd"])
        self.assertLess(agentic["budget"]["max_usd"], frontier["budget"]["max_usd"])

        capped = resolve_policy_route(
            DEFAULT_MODEL_POLICY,
            role_kind="runner",
            tier="frontier",
            caller_budget={"max_tokens": 123, "max_usd": 0.5, "max_depth": 9},
        )
        self.assertIsNotNone(capped)
        self.assertEqual(
            capped["budget"],
            {
                "max_tokens": 123,
                "max_usd": 0.5,
                "max_depth": frontier["budget"]["max_depth"],
            },
        )

    def test_unmapped_combo_resolves_to_none(self) -> None:
        route = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="scout", tier="quick"
        )
        self.assertIsNone(route)

    def test_resolution_only_ever_takes_first_candidate(self) -> None:
        policy = {
            "kind": MODEL_POLICY_KIND,
            "schema_version": MODEL_POLICY_SCHEMA_VERSION,
            "routes": [
                {
                    "role_kind": "runner",
                    "tier": "quick",
                    "budget": {"max_tokens": 1, "max_usd": 1.0, "max_depth": 1},
                    "candidates": [
                        {"adapter": "codex", "model": "first"},
                        {"adapter": "claude", "model": "second"},
                    ],
                }
            ],
        }
        route = resolve_policy_route(policy, role_kind="runner", tier="quick")
        self.assertIsNotNone(route)
        self.assertEqual(route["adapter"], "codex")
        self.assertEqual(route["model"], "first")

    def test_route_with_empty_candidates_resolves_to_none(self) -> None:
        policy = {
            "kind": MODEL_POLICY_KIND,
            "schema_version": MODEL_POLICY_SCHEMA_VERSION,
            "routes": [{"role_kind": "runner", "tier": "quick", "candidates": []}],
        }
        route = resolve_policy_route(policy, role_kind="runner", tier="quick")
        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()
