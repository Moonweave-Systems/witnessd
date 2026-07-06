from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from witnessd.orro_workstyle import advise_workstyle


class OrroProductRealityTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    MANIFEST = ROOT / "docs" / "orro-reality-check" / "manifest.json"

    def _manifest(self) -> dict:
        return json.loads(self.MANIFEST.read_text(encoding="utf-8"))

    def _scenario(self, name: str) -> dict:
        scenarios = self._manifest()["scenarios"]
        for scenario in scenarios:
            if scenario["name"] == name:
                return scenario
        self.fail(f"missing scenario {name}")

    def _advise(self, scenario_name: str) -> dict:
        scenario = self._scenario(scenario_name)
        return advise_workstyle(scenario["goal"], repo=self.ROOT, home=self.ROOT / ".witnessd")

    def _phases(self, decision: dict) -> list[str]:
        return [step["phase"] for step in decision["recommended_path"]]

    def _skipped_actions(self, decision: dict) -> set[str]:
        return {item["action"] for item in decision["actions_to_skip"]}

    def test_manifest_is_valid_json_with_required_scenarios(self) -> None:
        payload = self._manifest()

        self.assertEqual(payload["kind"], "orro-product-reality-check-manifest")
        self.assertEqual(payload["schema_version"], "0.1")
        self.assertFalse(payload["boundary"]["executes_commands"])
        self.assertFalse(payload["boundary"]["verifies_evidence"])
        self.assertFalse(payload["boundary"]["approves_merge"])
        self.assertFalse(payload["boundary"]["raises_assurance"])

        names = {scenario["name"] for scenario in payload["scenarios"]}
        self.assertEqual(
            names,
            {
                "trivial-doc-fix",
                "docs-change",
                "code-change",
                "review-only",
                "verification-only",
                "release-readiness",
                "risky-change",
                "scout-only-blocked",
                "stale-verdict-blocked",
            },
        )

    def test_every_scenario_has_required_fields(self) -> None:
        required = {
            "name",
            "goal",
            "expected_task_class",
            "expected_profile",
            "expected_effort",
            "should_recommend_proofrun",
            "should_recommend_proofcheck",
            "should_recommend_handoff_without_proofcheck",
            "human_review_required",
            "success_criteria",
        }

        for scenario in self._manifest()["scenarios"]:
            with self.subTest(scenario=scenario["name"]):
                self.assertTrue(required.issubset(scenario))
                self.assertIsInstance(scenario["success_criteria"], list)
                self.assertGreater(len(scenario["success_criteria"]), 0)

    def test_review_only_does_not_recommend_proofrun(self) -> None:
        decision = self._advise("review-only")

        self.assertEqual(decision["task_class"], "review-only")
        self.assertEqual(decision["recommended_profile"], "review-only")
        self.assertNotIn("proofrun", self._phases(decision))

    def test_verification_only_recommends_proofcheck_over_proofrun(self) -> None:
        decision = self._advise("verification-only")
        phases = self._phases(decision)

        self.assertEqual(decision["task_class"], "verification-only")
        self.assertIn("proofcheck", phases)
        self.assertNotIn("proofrun", phases)

    def test_trivial_doc_fix_uses_minimal_effort_and_skips_team_execution(self) -> None:
        decision = self._advise("trivial-doc-fix")

        self.assertEqual(decision["task_class"], "trivial-change")
        self.assertEqual(decision["recommended_effort"], "minimal")
        self.assertNotIn("proofrun", self._phases(decision))
        self.assertIn("role-lane team execution", self._skipped_actions(decision))

    def test_risky_change_requires_human_review_and_skips_auto_proofrun(self) -> None:
        decision = self._advise("risky-change")

        self.assertEqual(decision["task_class"], "risky-change")
        self.assertTrue(decision["human_review_required"])
        self.assertIn("auto proofrun", self._skipped_actions(decision))
        self.assertFalse(decision["boundary"]["raises_assurance"])

    def test_scout_only_and_stale_verdict_scenarios_encode_blocking_gates(self) -> None:
        scout = self._scenario("scout-only-blocked")
        stale = self._scenario("stale-verdict-blocked")

        self.assertIn("scout-only artifacts must not pass proofcheck", scout["success_criteria"])
        self.assertIn("stale verdict must block handoff", stale["success_criteria"])
        self.assertFalse(scout["should_recommend_handoff_without_proofcheck"])
        self.assertFalse(stale["should_recommend_handoff_without_proofcheck"])

    def test_checker_script_passes_current_manifest(self) -> None:
        result = subprocess.run(
            ["python3", "scripts/check_orro_product_reality.py"],
            cwd=self.ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_orro_product_reality: pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
