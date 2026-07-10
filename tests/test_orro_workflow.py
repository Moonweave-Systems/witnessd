from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.orro_workflow import (
    OrroWorkflowError,
    assert_workflow_phase_allowed,
    compile_workflow_plan,
)


class OrroWorkflowTests(unittest.TestCase):
    def _flowplan(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "flowplan", *args])
        return code, json.loads(stdout.getvalue())

    def test_code_change_profile_emits_orro_workflow_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, payload = self._flowplan(
                ["fix bug in parser", "--root", str(root), "--profile", "code-change"]
            )

            self.assertEqual(code, 0)
            plan = payload["workflow_plan"]
            self.assertEqual(plan["kind"], "orro-workflow-plan")
            self.assertEqual(plan["schema_version"], "0.1")
            self.assertEqual(plan["goal"], "fix bug in parser")
            self.assertEqual(plan["profile"], "code-change")
            self.assertEqual(
                plan["flow"],
                ["scout", "flowplan", "proofrun", "proofcheck", "handoff"],
            )
            self.assertTrue(plan["boundary"]["depone_verifies"])
            self.assertTrue(plan["boundary"]["witnessd_executes"])
            self.assertTrue(plan["boundary"]["orro_exposes_workflow"])
            self.assertFalse(plan["boundary"]["orro_is_third_engine"])
            self.assertIn("engine-lock", plan["forbidden_assurance_sources"])
            self.assertIn("doctor readiness", plan["forbidden_assurance_sources"])
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self.assertFalse((root / "team-ledger.json").exists())

    def test_review_only_profile_does_not_claim_execution_happened(self) -> None:
        code, payload = self._flowplan(
            ["review this PR", "--root", ".", "--profile", "review-only"]
        )

        self.assertEqual(code, 0)
        plan = payload["workflow_plan"]
        self.assertEqual(plan["profile"], "review-only")
        self.assertFalse(any(role["may_execute"] for role in plan["roles"]))
        self.assertNotIn("proofrun", plan["flow"])
        self.assertFalse(any(call["phase"] == "proofrun" for call in plan["engine_calls"]))
        self.assertNotIn("proofrun emits evidence", plan["required_gates"])
        self.assertIn("review-only handoff is intent; formal ORRO handoff still requires proofcheck", plan["required_gates"])
        self.assertIn("model confidence", plan["forbidden_assurance_sources"])

    def test_verification_only_profile_delegates_verification_without_execution(self) -> None:
        code, payload = self._flowplan(
            ["verify this evidence", "--root", ".", "--profile", "verification-only"]
        )

        self.assertEqual(code, 0)
        plan = payload["workflow_plan"]
        self.assertEqual(plan["profile"], "verification-only")
        proofcheck = next(call for call in plan["engine_calls"] if call["phase"] == "proofcheck")
        self.assertEqual(proofcheck["engine"], "Depone")
        self.assertFalse(proofcheck["executes"])
        self.assertTrue(proofcheck["verifies"])
        self.assertFalse(any(call["executes"] for call in plan["engine_calls"]))

    def test_workflow_phase_gate_allows_only_declared_execution_phase(self) -> None:
        code_change = compile_workflow_plan(goal="fix parser", profile="code-change")
        assert_workflow_phase_allowed(code_change, "proofrun")

        review_only = compile_workflow_plan(goal="review this PR", profile="review-only")
        with self.assertRaises(OrroWorkflowError) as cm:
            assert_workflow_phase_allowed(review_only, "proofrun")
        self.assertEqual(cm.exception.code, "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")

        verification_only = compile_workflow_plan(goal="verify evidence", profile="verification-only")
        with self.assertRaises(OrroWorkflowError) as cm:
            assert_workflow_phase_allowed(verification_only, "proofrun")
        self.assertEqual(cm.exception.code, "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")

    def test_docs_change_requires_evidence_gates_before_handoff_when_executing(self) -> None:
        code, payload = self._flowplan(
            ["update docs", "--root", ".", "--profile", "docs-change"]
        )

        self.assertEqual(code, 0)
        gates = payload["workflow_plan"]["required_gates"]
        self.assertIn("proofrun emits evidence", gates)
        self.assertIn("proofcheck writes proofcheck-verdict.json", gates)
        self.assertIn("handoff requires passing bound proofcheck verdict", gates)

    def test_release_readiness_profile_lists_readiness_as_non_assurance(self) -> None:
        code, payload = self._flowplan(
            ["prepare release", "--root", ".", "--profile", "release-readiness"]
        )

        self.assertEqual(code, 0)
        plan = payload["workflow_plan"]
        phases = [call["phase"] for call in plan["engine_calls"]]
        self.assertIn("init", phases)
        self.assertIn("doctor", phases)
        self.assertIn("engine-lock", phases)
        self.assertIn("doctor readiness", plan["forbidden_assurance_sources"])
        self.assertIn("engine-lock", plan["forbidden_assurance_sources"])
        for role in plan["roles"]:
            self.assertFalse(role["raises_assurance"])

    def test_flowplan_out_writes_same_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "workflow-plan.json"
            code, payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--out",
                    str(out),
                ]
            )

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)

    def test_flowplan_role_lanes_out_writes_executable_intent_for_code_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "role-lane-plan.json"
            code, payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(role_lanes["kind"], "orro-role-lane-plan")
            self.assertEqual(role_lanes["schema_version"], "0.1")
            self.assertEqual(role_lanes["workflow_profile"], "code-change")
            self.assertEqual(role_lanes["goal"], "fix parser")
            self.assertTrue(role_lanes["execution_allowed"])
            self.assertRegex(role_lanes["workflow_plan_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual(payload["role_lane_plan"]["path"], str(out.resolve(strict=False)))
            self.assertRegex(payload["role_lane_plan"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                payload["role_lane_plan"]["workflow_plan_hash"],
                role_lanes["workflow_plan_hash"],
            )
            self.assertFalse(role_lanes["boundary"]["role_lane_plan_is_proof"])
            self.assertFalse(role_lanes["boundary"]["raises_assurance"])
            self.assertFalse(role_lanes["boundary"]["approves_merge"])
            self.assertGreaterEqual(len(role_lanes["lanes"]), 1)
            for lane in role_lanes["lanes"]:
                self.assertEqual(lane["adapter"], "shell")
                self.assertTrue(lane["may_execute"])
                self.assertFalse(lane["may_verify"])
                self.assertFalse(lane["raises_assurance"])
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self.assertFalse((root / "team-ledger.json").exists())

    def test_flowplan_role_lanes_review_only_can_route_to_gemini_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "review safely",
                    "--root",
                    tmp,
                    "--profile",
                    "review-only",
                    "--role-lanes-out",
                    str(out),
                    "--lane-adapter",
                    "gemini",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(role_lanes["execution_allowed"])
            self.assertEqual(role_lanes["workflow_profile"], "review-only")
            self.assertEqual(len(role_lanes["lanes"]), 1)
            lane = role_lanes["lanes"][0]
            self.assertEqual(lane["role_id"], "reviewer")
            self.assertEqual(lane["adapter"], "gemini")
            self.assertFalse(lane["may_execute"])
            self.assertFalse(lane["may_verify"])
            self.assertEqual(lane["phase"], "review")

    def test_flowplan_role_lanes_profiles_block_non_execution_profiles(self) -> None:
        for profile in ("verification-only", "release-readiness"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "role-lane-plan.json"
                code, _payload = self._flowplan(
                    [
                        "review safely",
                        "--root",
                        tmp,
                        "--profile",
                        profile,
                        "--role-lanes-out",
                        str(out),
                    ]
                )

                self.assertEqual(code, 0)
                role_lanes = json.loads(out.read_text(encoding="utf-8"))
                self.assertFalse(role_lanes["execution_allowed"])
                self.assertEqual(role_lanes["lanes"], [])

    def test_flowplan_role_lanes_docs_change_is_executable_plan_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "update docs",
                    "--root",
                    tmp,
                    "--profile",
                    "docs-change",
                    "--role-lanes-out",
                    str(out),
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(role_lanes["execution_allowed"])
            self.assertEqual(role_lanes["workflow_profile"], "docs-change")
            self.assertGreaterEqual(len(role_lanes["lanes"]), 1)
            self.assertFalse((Path(tmp) / ".witnessd" / "runs").exists())

    def test_flowplan_role_lanes_invalid_adapter_fails_closed(self) -> None:
        with self.assertRaises(SystemExit):
            main(
                [
                    "orro",
                    "flowplan",
                    "bad adapter",
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    "role-lane-plan.json",
                    "--lane-adapter",
                    "networked",
                ]
            )

    def test_invalid_profile_fails_closed(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "orro",
                    "flowplan",
                    "unknown work",
                    "--profile",
                    "live-agent",
                    "--root",
                    ".",
                    "--json",
                ]
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["error"]["code"], "ERR_ORRO_WORKFLOW_PROFILE_UNKNOWN")

    def test_flowplan_remains_plan_only_and_rejects_draft_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, payload = self._flowplan(
                ["plan without execution", "--root", str(root), "--profile", "code-change"]
            )

            self.assertEqual(code, 0)
            self.assertIn("sealed_plan", payload)
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self.assertFalse((root / "team-ledger.json").exists())
            self.assertFalse((root / "proofcheck-verdict.json").exists())

        with self.assertRaises(SystemExit):
            main(["orro", "flowplan", "bad", "--draft-adapter", "codex"])


if __name__ == "__main__":
    unittest.main()
