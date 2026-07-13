from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from witnessd.model_routing_benchmark import (
    MODEL_ROUTING_BENCHMARK_KIND,
    default_task_suite,
    plan_measurements,
)
from witnessd.model_policy import DEFAULT_MODEL_POLICY, resolve_policy_route


class ModelRoutingBenchmarkTests(unittest.TestCase):
    def test_default_suite_has_executable_tasks_for_every_role_and_tier(self) -> None:
        suite = default_task_suite()

        self.assertEqual(len(suite), 24)
        self.assertGreaterEqual(len(suite), 20)
        self.assertLessEqual(len(suite), 30)
        self.assertEqual(
            {(task.role_kind, task.tier) for task in suite},
            {
                (role_kind, tier)
                for role_kind in ("runner", "reviewer")
                for tier in ("quick", "agentic", "frontier")
            },
        )
        for task in suite:
            task_payload = task.as_dict()
            self.assertTrue(task_payload["goal"])
            self.assertTrue(task_payload["repo_state"]["files"])
            self.assertIn(
                task_payload["expected_verification"]["kind"],
                {"file_content", "read_only_review"},
            )
            self.assertIn(task_payload["comparative_value"]["primary_role"], {"runner", "reviewer"})

    def test_default_suite_routes_and_budgets_without_live_calls(self) -> None:
        suite = default_task_suite()
        payload = plan_measurements(suite)

        self.assertEqual(payload["kind"], MODEL_ROUTING_BENCHMARK_KIND)
        self.assertEqual(payload["schema_version"], "0.2")
        self.assertFalse(payload["boundary"]["proof"])
        self.assertFalse(payload["boundary"]["assurance"])
        self.assertFalse(payload["boundary"]["benchmark_claim"])
        self.assertFalse(payload["boundary"]["verifier_truth"])
        self.assertFalse(payload["boundary"]["fallback_observation_complete"])
        self.assertFalse(payload["boundary"]["multi_candidate_fallback_enabled"])
        self.assertFalse(payload["live"])
        self.assertEqual(len(payload["tasks"]), len(suite))
        for task in payload["tasks"]:
            self.assertEqual(task["status"], "planned")
            self.assertIn(task["route"]["adapter"], {"codex", "agy"})
            self.assertIn("model", task["route"])
            self.assertGreater(task["budget"]["max_tokens"], 0)
            self.assertGreater(task["budget"]["max_usd"], 0.0)
            self.assertTrue(task["budget_compliant"])

    def test_live_record_is_constructed_from_injected_execution_result(self) -> None:
        from witnessd.model_routing_benchmark import (
            build_live_measurement_record,
            compare_role_value,
        )

        task = default_task_suite()[0]
        route = resolve_policy_route(
            DEFAULT_MODEL_POLICY,
            role_kind=task.role_kind,
            tier=task.tier,
        )
        self.assertIsNotNone(route)

        record = build_live_measurement_record(
            task,
            route=route or {},
            execution={
                "status": "measured",
                "success": True,
                "verifier": {
                    "engine": "depone",
                    "command": "agent-fabric-verify-signature",
                    "decision": "pass",
                    "exit_code": 0,
                },
                "elapsed_seconds": 1.25,
                "turn_count": 2,
                "input_tokens": 120,
                "output_tokens": 30,
                "estimated_cost_usd": 0.004,
                "fallback_receipts": [],
                "unavailable_model_receipts": [],
                "declared_model": "gpt-5.6-luna",
                "actual_model": "gpt-5.6-luna",
                "model_verification_status": "verified",
                "touched_files": list(task.expected_touched_files),
                "evidence_dir": "/tmp/evidence",
                "blocked_reason": None,
            },
        )

        self.assertTrue(record["success"])
        self.assertEqual(record["verifier"]["decision"], "pass")
        self.assertEqual(record["elapsed_seconds"], 1.25)
        self.assertEqual(record["turn_count"], 2)
        self.assertEqual(
            record["tokens"], {"input": 120, "output": 30, "total": 150}
        )
        self.assertEqual(record["estimated_cost_usd"], 0.004)
        self.assertEqual(
            record["cost_estimate_method"],
            "task_prediction_proportional_to_observed_tokens",
        )
        self.assertEqual(record["fallback_receipts"], [])
        self.assertEqual(record["comparative_value"]["primary_role"], "runner")
        self.assertTrue(record["comparative_value"]["task_success"])
        comparison = compare_role_value([record])
        self.assertEqual(comparison["roles"]["runner"]["task_count"], 1)
        self.assertEqual(comparison["roles"]["runner"]["success_rate"], 1.0)
        self.assertEqual(comparison["roles"]["runner"]["mean_total_tokens"], 150.0)
        self.assertEqual(comparison["roles"]["reviewer"]["task_count"], 0)

    def test_budget_derivation_never_increases_policy_ceiling(self) -> None:
        from witnessd.model_routing_benchmark import (
            build_live_measurement_record,
            derive_tier_budgets,
        )

        task = default_task_suite()[0]
        measurement = build_live_measurement_record(
            task,
            route=resolve_policy_route(
                DEFAULT_MODEL_POLICY,
                role_kind=task.role_kind,
                tier=task.tier,
            )
            or {},
            execution={
                "status": "measured",
                "success": True,
                "verifier": {"engine": "depone", "decision": "pass"},
                "elapsed_seconds": 45.0,
                "turn_count": 3,
                "input_tokens": 190000,
                "output_tokens": 30000,
                "estimated_cost_usd": 1.2,
                "fallback_receipts": [],
                "unavailable_model_receipts": [],
                "declared_model": "gpt-5.6-luna",
                "actual_model": "gpt-5.6-luna",
                "model_verification_status": "verified",
                "touched_files": list(task.expected_touched_files),
                "evidence_dir": "/tmp/evidence",
                "blocked_reason": None,
            },
        )

        advisory = derive_tier_budgets([measurement], headroom=2.0)
        proposed = advisory["tiers"][0]
        route = resolve_policy_route(
            DEFAULT_MODEL_POLICY,
            role_kind=task.role_kind,
            tier=task.tier,
        )
        self.assertIsNotNone(route)
        policy_budget = (route or {})["budget"]
        self.assertLessEqual(proposed["proposed_budget"]["max_tokens"], policy_budget["max_tokens"])
        self.assertLessEqual(proposed["proposed_budget"]["max_usd"], policy_budget["max_usd"])
        self.assertEqual(proposed["proposed_budget"]["max_depth"], policy_budget["max_depth"])
        self.assertTrue(proposed["within_policy_ceiling"])
        self.assertFalse(advisory["boundary"]["changes_model_policy"])
        self.assertFalse(advisory["boundary"]["benchmark_claim"])

    def test_reviewer_success_requires_a_structured_finding_for_seeded_file(self) -> None:
        from witnessd.model_routing_benchmark import _task_outcome

        task = next(task for task in default_task_suite() if task.role_kind == "reviewer")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = root / "transcript.jsonl"
            transcript.write_text('{"message": "review complete"}\n', encoding="utf-8")
            receipt = root / "review-receipt.json"
            runner_receipt = {"exit_code": 0, "touched_files": []}

            receipt.write_text(
                json.dumps({"kind": "moonweave-review-receipt", "findings": []}),
                encoding="utf-8",
            )
            self.assertFalse(
                _task_outcome(
                    task,
                    repo=root,
                    runner_receipt=runner_receipt,
                    transcript_path=transcript,
                    review_receipt_path=receipt,
                )
            )

            receipt.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-review-receipt",
                        "findings": [
                            {
                                "severity": "high",
                                "file": task.expected_verification["path"],
                                "line": 1,
                                "summary": "seeded correctness risk",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                _task_outcome(
                    task,
                    repo=root,
                    runner_receipt=runner_receipt,
                    transcript_path=transcript,
                    review_receipt_path=receipt,
                )
            )

    def test_model_receipts_match_observable_declaration_states(self) -> None:
        from witnessd.model_routing_benchmark import (
            _actual_model_from_declaration,
            _model_receipts,
        )

        verified = {
            "requested_model": "gpt-5.6-luna",
            "verification_status": "verified",
        }
        actual = _actual_model_from_declaration(verified)
        fallback, unavailable = _model_receipts(
            declared_model="gpt-5.6-luna",
            actual_model=actual,
            verification_status="verified",
            blocked_reason=None,
        )
        self.assertEqual(actual, "gpt-5.6-luna")
        self.assertEqual(fallback, [])
        self.assertEqual(unavailable, [])

        for status in ("requested-unverified", "rejected"):
            with self.subTest(status=status):
                declaration = {
                    "requested_model": "gemini-3.5-flash",
                    "verification_status": status,
                }
                actual = _actual_model_from_declaration(declaration)
                fallback, unavailable = _model_receipts(
                    declared_model="gemini-3.5-flash",
                    actual_model=actual,
                    verification_status=status,
                    blocked_reason=None,
                )
                self.assertIsNone(actual)
                self.assertEqual(fallback, [])
                self.assertEqual(len(unavailable), 1)
                self.assertFalse(unavailable[0]["fallback_attempted"])


if __name__ == "__main__":
    unittest.main()
