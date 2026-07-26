from __future__ import annotations

import os
import subprocess
import unittest

from witnessd.role_capability import RolepackError, RoleCapabilityGrant


class GeminiRetirementTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        return subprocess.run(
            ["/usr/bin/python3", "-m", "witnessd", *args],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            text=True,
            capture_output=True,
        )

    def test_flowplan_gemini_is_a_prelaunch_migration_error(self) -> None:
        result = self._run(
            "orro",
            "flowplan",
            "audit routing",
            "--lane-adapter",
            "gemini",
            "--json",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERR_GEMINI_ADAPTER_RETIRED", result.stderr)
        self.assertIn("agy", result.stderr)
        self.assertIn("python3 -m orro flowplan", result.stderr)

    def test_review_gemini_binary_is_a_prelaunch_migration_error(self) -> None:
        result = self._run(
            "orro",
            "review",
            "--repo",
            ".",
            "--role-lane-plan",
            "/tmp/role-lane-plan.json",
            "--gemini-binary",
            "/missing/gemini",
            "--json",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERR_GEMINI_ADAPTER_RETIRED", result.stderr)
        self.assertIn("agy", result.stderr)
        self.assertIn("python3 -m orro review", result.stderr)

    def test_role_capability_rejects_gemini_as_retired(self) -> None:
        with self.assertRaises(RolepackError) as ctx:
            RoleCapabilityGrant.from_dict(
                {
                    "role_id": "reviewer",
                    "capability": "review",
                    "adapters": ["gemini"],
                }
            )

        self.assertEqual(ctx.exception.code, "ERR_GEMINI_ADAPTER_RETIRED")
        self.assertIn("agy", ctx.exception.message)
