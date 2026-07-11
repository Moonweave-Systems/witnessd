from __future__ import annotations

import unittest

from witnessd.role_capability import (
    DEFAULT_DEVELOPER_ROLEPACK,
    ROLEPACK_KIND,
    ROLEPACK_SCHEMA_VERSION,
    RoleCapabilityGrant,
    grant_for_role,
    validate_rolepack,
)


class RoleCapabilityTests(unittest.TestCase):
    def test_default_developer_rolepack_grants_runner_and_reviewer(self) -> None:
        validate_rolepack(DEFAULT_DEVELOPER_ROLEPACK)

        runner = grant_for_role(DEFAULT_DEVELOPER_ROLEPACK, "runner")
        reviewer = grant_for_role(DEFAULT_DEVELOPER_ROLEPACK, "reviewer")

        self.assertIsNotNone(runner)
        self.assertIsNotNone(reviewer)
        self.assertEqual(runner.role_id, "runner")
        self.assertEqual(runner.capability, "execute")
        self.assertEqual(runner.adapters, ("shell", "codex", "claude", "opencode"))
        self.assertEqual(runner.model_policy_ref, "default")
        self.assertEqual(runner.write_scope, ("orro/**", "docs/**"))
        self.assertEqual(runner.tools, {"mcp": (), "allow": ()})
        self.assertEqual(reviewer.role_id, "reviewer")
        self.assertEqual(reviewer.capability, "review")
        self.assertEqual(reviewer.adapters, ("agy", "gemini"))
        self.assertEqual(reviewer.model_policy_ref, "default")
        self.assertEqual(reviewer.write_scope, ())
        self.assertEqual(reviewer.tools, {"mcp": (), "allow": ()})

    def test_rolepack_accepts_s3_tools_grant(self) -> None:
        rolepack = {
            "kind": ROLEPACK_KIND,
            "schema_version": ROLEPACK_SCHEMA_VERSION,
            "name": "developer",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model_policy_ref": "default",
                    "write_scope": ["src/**"],
                    "tools": {"mcp": ["filesystem"], "allow": ["read_file"]},
                }
            ],
        }

        validate_rolepack(rolepack)
        grant = grant_for_role(rolepack, "runner")
        self.assertIsNotNone(grant)
        self.assertEqual(grant.tools, {"mcp": ("filesystem",), "allow": ("read_file",)})

    def test_role_capability_grant_rejects_invalid_tools(self) -> None:
        with self.assertRaises(ValueError):
            RoleCapabilityGrant.from_dict(
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model_policy_ref": "default",
                    "write_scope": ["src/**"],
                    "tools": {"mcp": ["filesystem"], "allow": [""]},
                }
            )

    def test_rolepack_rejects_s1_unknown_top_level_fields(self) -> None:
        rolepack = {
            "kind": ROLEPACK_KIND,
            "schema_version": ROLEPACK_SCHEMA_VERSION,
            "name": "developer",
            "profile": "code-change",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model_policy_ref": "default",
                    "write_scope": ["src/**"],
                }
            ],
        }

        with self.assertRaises(ValueError):
            validate_rolepack(rolepack)

    def test_role_capability_grant_from_dict_rejects_unknown_capability(self) -> None:
        with self.assertRaises(ValueError):
            RoleCapabilityGrant.from_dict(
                {
                    "role_id": "runner",
                    "capability": "admin",
                    "adapters": ["codex"],
                    "model_policy_ref": "default",
                    "write_scope": ["src/**"],
                }
            )

    def test_role_capability_grant_rejects_invalid_write_scope(self) -> None:
        with self.assertRaises(ValueError):
            RoleCapabilityGrant.from_dict(
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model_policy_ref": "default",
                    "write_scope": ["src/**", ""],
                }
            )


if __name__ == "__main__":
    unittest.main()
