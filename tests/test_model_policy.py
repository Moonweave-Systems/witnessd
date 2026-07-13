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
            ("runner", "quick"): {"adapter": "codex", "model": "gpt-5.6-luna"},
            ("runner", "agentic"): {"adapter": "codex", "model": "gpt-5.6-sol"},
            ("runner", "frontier"): {"adapter": "codex", "model": "gpt-5.6-sol"},
            ("reviewer", "quick"): {"adapter": "agy", "model": "gemini-3.5-flash"},
            ("reviewer", "agentic"): {"adapter": "agy", "model": "gemini-3.5-flash"},
            ("reviewer", "frontier"): {"adapter": "agy", "model": "gemini-3.5-flash"},
        }
        actual = {
            (role_kind, tier): resolve_policy_route(
                DEFAULT_MODEL_POLICY, role_kind=role_kind, tier=tier
            )
            for role_kind in ("runner", "reviewer")
            for tier in ("quick", "agentic", "frontier")
        }
        self.assertEqual(actual, expected)

    def test_resolves_reviewer_quick_to_agy(self) -> None:
        route = resolve_policy_route(
            DEFAULT_MODEL_POLICY, role_kind="reviewer", tier="quick"
        )
        self.assertEqual(route, {"adapter": "agy", "model": "gemini-3.5-flash"})

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
                    "candidates": [
                        {"adapter": "codex", "model": "first"},
                        {"adapter": "claude", "model": "second"},
                    ],
                }
            ],
        }
        route = resolve_policy_route(policy, role_kind="runner", tier="quick")
        self.assertEqual(route, {"adapter": "codex", "model": "first"})

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
