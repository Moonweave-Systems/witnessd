from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.model_policy import DEFAULT_MODEL_POLICY
from witnessd.orro_workflow import (
    OrroWorkflowError,
    assert_workflow_phase_allowed,
    compile_role_lane_plan,
    compile_workflow_plan,
    validate_role_lane_plan,
)


class OrroWorkflowTests(unittest.TestCase):
    def _flowplan(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "flowplan", *args])
        return code, json.loads(stdout.getvalue())

    def _flowplan_raw(self, args: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "flowplan", *args])
        return code, stdout.getvalue()

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
        self.assertFalse(
            any(call["phase"] == "proofrun" for call in plan["engine_calls"])
        )
        self.assertNotIn("proofrun emits evidence", plan["required_gates"])
        self.assertIn(
            "review-only handoff is intent; formal ORRO handoff still requires proofcheck",
            plan["required_gates"],
        )
        self.assertIn("model confidence", plan["forbidden_assurance_sources"])

    def test_verification_only_profile_delegates_verification_without_execution(
        self,
    ) -> None:
        code, payload = self._flowplan(
            ["verify this evidence", "--root", ".", "--profile", "verification-only"]
        )

        self.assertEqual(code, 0)
        plan = payload["workflow_plan"]
        self.assertEqual(plan["profile"], "verification-only")
        proofcheck = next(
            call for call in plan["engine_calls"] if call["phase"] == "proofcheck"
        )
        self.assertEqual(proofcheck["engine"], "Depone")
        self.assertFalse(proofcheck["executes"])
        self.assertTrue(proofcheck["verifies"])
        self.assertFalse(any(call["executes"] for call in plan["engine_calls"]))

    def test_workflow_phase_gate_allows_only_declared_execution_phase(self) -> None:
        code_change = compile_workflow_plan(goal="fix parser", profile="code-change")
        assert_workflow_phase_allowed(code_change, "proofrun")

        review_only = compile_workflow_plan(
            goal="review this PR", profile="review-only"
        )
        with self.assertRaises(OrroWorkflowError) as cm:
            assert_workflow_phase_allowed(review_only, "proofrun")
        self.assertEqual(cm.exception.code, "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")

        verification_only = compile_workflow_plan(
            goal="verify evidence", profile="verification-only"
        )
        with self.assertRaises(OrroWorkflowError) as cm:
            assert_workflow_phase_allowed(verification_only, "proofrun")
        self.assertEqual(cm.exception.code, "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")

    def test_docs_change_requires_evidence_gates_before_handoff_when_executing(
        self,
    ) -> None:
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

    def test_flowplan_role_lanes_out_writes_executable_intent_for_code_change(
        self,
    ) -> None:
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
            self.assertEqual(
                payload["role_lane_plan"]["path"], str(out.resolve(strict=False))
            )
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

    def test_flowplan_role_lanes_review_only_can_route_to_agy_reviewer(self) -> None:
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
                    "agy",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(role_lanes["execution_allowed"])
            self.assertEqual(role_lanes["workflow_profile"], "review-only")
            self.assertEqual(len(role_lanes["lanes"]), 1)
            lane = role_lanes["lanes"][0]
            self.assertEqual(lane["role_id"], "reviewer")
            self.assertEqual(lane["adapter"], "agy")
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

    def test_flowplan_role_lanes_default_policy_off_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            for lane in role_lanes["lanes"]:
                self.assertEqual(lane["tier"], "quick")
                self.assertNotIn("model", lane)
                self.assertNotIn("resolved_via_policy", lane)
                self.assertNotIn("granted_adapters", lane)
                self.assertNotIn("granted_write_scope", lane)
                self.assertNotIn("granted_tools", lane)

    def test_flowplan_rolepack_developer_inlines_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--model-policy",
                    "default",
                    "--role-lane-tier",
                    "frontier",
                    "--rolepack",
                    "developer",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            lane = role_lanes["lanes"][0]
            self.assertEqual(lane["adapter"], "codex")
            self.assertEqual(lane["model"], "gpt-5.5")
            self.assertEqual(lane["model_source"], "rolepack")
            self.assertEqual(lane["granted_adapters"], ["codex"])
            self.assertEqual(lane["granted_write_scope"], ["orro/**", "docs/**"])
            self.assertEqual(lane["granted_tools"], {"mcp": [], "allow": []})
            self.assertEqual(lane["role_capability"]["role_id"], "runner")

    def test_flowplan_unknown_rolepack_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, text = self._flowplan_raw(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--rolepack",
                    "designer",
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            payload = json.loads(text)
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_ROLEPACK_UNKNOWN")
            self.assertFalse(out.exists())

    def test_flowplan_rolepack_file_loads_custom_rolepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rolepack_path = root / "rolepack.json"
            out = root / "role-lane-plan.json"
            rolepack_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "custom",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["shell"],
                                "model": "custom-runner-model",
                                "write_scope": ["orro/**"],
                                "tools": {"mcp": [], "allow": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, _payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--rolepack-file",
                    str(rolepack_path),
                ]
            )

            self.assertEqual(code, 0)
            lane = json.loads(out.read_text(encoding="utf-8"))["lanes"][0]
            self.assertEqual(lane["granted_adapters"], ["shell"])
            self.assertEqual(lane["model"], "custom-runner-model")
            self.assertEqual(lane["model_source"], "rolepack")

    def test_flowplan_team_file_loads_custom_rolepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team_path = root / ".orro" / "team.json"
            out = root / "role-lane-plan.json"
            team_path.parent.mkdir()
            team_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "custom-team",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["shell"],
                                "model": "team-runner-model",
                                "write_scope": ["orro/**"],
                                "tools": {"mcp": [], "allow": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, _payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--team",
                    str(team_path),
                ]
            )

            self.assertEqual(code, 0)
            lane = json.loads(out.read_text(encoding="utf-8"))["lanes"][0]
            self.assertEqual(lane["role_capability"]["model"], "team-runner-model")
            self.assertEqual(lane["model"], "team-runner-model")

    def test_flowplan_team_file_selects_per_role_adapter_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team_path = root / ".orro" / "team.json"
            runner_out = root / "runner-role-lane-plan.json"
            reviewer_out = root / "reviewer-role-lane-plan.json"
            team_path.parent.mkdir()
            team_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "custom-team",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["codex"],
                                "model": "gpt-5.5",
                                "write_scope": ["orro/**"],
                                "tools": {"mcp": [], "allow": []},
                            },
                            {
                                "role_id": "reviewer",
                                "capability": "review",
                                "adapters": ["agy"],
                                "model": "gemini-3.1-pro",
                                "write_scope": [],
                                "tools": {"mcp": [], "allow": []},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            runner_code, _runner_payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(runner_out),
                    "--team",
                    str(team_path),
                ]
            )
            reviewer_code, _reviewer_payload = self._flowplan(
                [
                    "review safely",
                    "--root",
                    tmp,
                    "--profile",
                    "review-only",
                    "--role-lanes-out",
                    str(reviewer_out),
                    "--team",
                    str(team_path),
                ]
            )

            self.assertEqual(runner_code, 0)
            self.assertEqual(reviewer_code, 0)
            runner_lane = json.loads(runner_out.read_text(encoding="utf-8"))["lanes"][0]
            reviewer_lane = json.loads(reviewer_out.read_text(encoding="utf-8"))[
                "lanes"
            ][0]
            self.assertEqual(runner_lane["role_id"], "runner")
            self.assertEqual(runner_lane["adapter"], "codex")
            self.assertEqual(runner_lane["model"], "gpt-5.5")
            self.assertEqual(runner_lane["model_source"], "rolepack")
            self.assertEqual(reviewer_lane["role_id"], "reviewer")
            self.assertEqual(reviewer_lane["adapter"], "agy")
            self.assertEqual(reviewer_lane["model"], "gemini-3.1-pro")
            self.assertEqual(reviewer_lane["model_source"], "rolepack")

    def test_flowplan_rolepack_name_and_file_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rolepack_path = Path(tmp) / "rolepack.json"
            rolepack_path.write_text("{}", encoding="utf-8")
            code, text = self._flowplan_raw(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(Path(tmp) / "role-lane-plan.json"),
                    "--rolepack",
                    "developer",
                    "--rolepack-file",
                    str(rolepack_path),
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            self.assertEqual(
                json.loads(text)["error"]["code"], "ERR_ORRO_ROLEPACK_CONFLICT"
            )

    def test_flowplan_rolepack_file_invalid_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rolepack_path = Path(tmp) / "rolepack.json"
            rolepack_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.1",
                        "name": "future",
                        "skillpack": "future-field",
                        "grants": [],
                    }
                ),
                encoding="utf-8",
            )
            code, text = self._flowplan_raw(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(Path(tmp) / "role-lane-plan.json"),
                    "--rolepack-file",
                    str(rolepack_path),
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            self.assertEqual(
                json.loads(text)["error"]["code"], "ERR_ORRO_ROLEPACK_INVALID"
            )

    def test_witnessd_flowplan_alias_accepts_rolepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "flowplan",
                        "fix parser",
                        "--root",
                        tmp,
                        "--profile",
                        "code-change",
                        "--role-lanes-out",
                        str(out),
                        "--rolepack",
                        "developer",
                    ]
                )

            self.assertEqual(code, 0)
            lane = json.loads(out.read_text(encoding="utf-8"))["lanes"][0]
            self.assertEqual(lane["role_capability"]["role_id"], "runner")

    def test_flowplan_role_lanes_model_policy_default_resolves_runner_to_codex(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "fix parser",
                    "--root",
                    tmp,
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--model-policy",
                    "default",
                    "--role-lane-tier",
                    "frontier",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            runner_lanes = [
                lane for lane in role_lanes["lanes"] if lane["role_id"] == "runner"
            ]
            self.assertEqual(len(runner_lanes), 1)
            lane = runner_lanes[0]
            self.assertEqual(lane["tier"], "frontier")
            self.assertEqual(lane["adapter"], "codex")
            self.assertEqual(lane["model"], "gpt-5.5")
            self.assertTrue(lane["resolved_via_policy"])
            self.assertEqual(lane["policy_role_kind"], "runner")
            self.assertEqual(lane["policy_tier"], "frontier")

    def test_flowplan_role_lanes_model_policy_default_resolves_reviewer_to_agy(
        self,
    ) -> None:
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
                    "--model-policy",
                    "default",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(role_lanes["lanes"]), 1)
            lane = role_lanes["lanes"][0]
            self.assertEqual(lane["role_id"], "reviewer")
            self.assertEqual(lane["tier"], "quick")
            self.assertEqual(lane["adapter"], "agy")
            self.assertEqual(lane["model"], "gemini-3.1-pro")
            self.assertTrue(lane["resolved_via_policy"])

    def test_compile_role_lane_plan_policy_unresolved_combo_fails_closed(self) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        incomplete_policy = {
            "kind": DEFAULT_MODEL_POLICY["kind"],
            "schema_version": DEFAULT_MODEL_POLICY["schema_version"],
            "routes": [],
        }
        with self.assertRaises(OrroWorkflowError) as ctx:
            compile_role_lane_plan(
                workflow_plan=workflow_plan, policy=incomplete_policy
            )
        self.assertEqual(ctx.exception.code, "ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED")

    def test_compile_role_lane_plan_rolepack_model_bypasses_policy_model_lookup(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        incomplete_policy = {
            "kind": DEFAULT_MODEL_POLICY["kind"],
            "schema_version": DEFAULT_MODEL_POLICY["schema_version"],
            "routes": [],
        }
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "developer",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["shell"],
                    "model": "custom-runner-model",
                    "write_scope": ["orro/**"],
                }
            ],
        }

        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            policy=incomplete_policy,
            rolepack=rolepack,
        )

        lane = role_lane_plan["lanes"][0]
        self.assertEqual(lane["adapter"], "shell")
        self.assertEqual(lane["model"], "custom-runner-model")
        self.assertEqual(lane["model_source"], "rolepack")
        self.assertNotIn("resolved_via_policy", lane)

    def test_compile_role_lane_plan_rolepack_model_requires_single_adapter(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "developer",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex", "claude"],
                    "model": "gpt-5.5",
                    "write_scope": ["orro/**"],
                }
            ],
        }

        with self.assertRaises(OrroWorkflowError) as ctx:
            compile_role_lane_plan(workflow_plan=workflow_plan, rolepack=rolepack)

        self.assertEqual(ctx.exception.code, "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED")

    def test_compile_role_lane_plan_policy_adapter_outside_role_grant_fails_closed(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        agy_runner_policy = {
            "kind": DEFAULT_MODEL_POLICY["kind"],
            "schema_version": DEFAULT_MODEL_POLICY["schema_version"],
            "routes": [
                {
                    "role_kind": "runner",
                    "tier": "quick",
                    "candidates": [{"adapter": "agy", "model": "review-model"}],
                }
            ],
        }
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "developer",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                }
            ],
        }

        with self.assertRaises(OrroWorkflowError) as ctx:
            compile_role_lane_plan(
                workflow_plan=workflow_plan,
                policy=agy_runner_policy,
                rolepack=rolepack,
            )

        self.assertEqual(ctx.exception.code, "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED")

    def test_compile_role_lane_plan_inlines_role_capability_when_rolepack_is_supplied(
        self,
    ) -> None:
        from witnessd.role_capability import DEFAULT_DEVELOPER_ROLEPACK

        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            tier="frontier",
            policy=DEFAULT_MODEL_POLICY,
            rolepack=DEFAULT_DEVELOPER_ROLEPACK,
        )

        validate_role_lane_plan(role_lane_plan)
        lane = role_lane_plan["lanes"][0]
        self.assertEqual(lane["adapter"], "codex")
        self.assertEqual(lane["model"], "gpt-5.5")
        self.assertEqual(lane["model_source"], "rolepack")
        self.assertEqual(lane["granted_adapters"], ["codex"])
        self.assertEqual(lane["role_capability"]["role_id"], "runner")
        self.assertEqual(lane["role_capability"]["capability"], "execute")
        self.assertNotIn("model_policy_ref", lane["role_capability"])
        self.assertEqual(lane["role_capability"]["model"], "gpt-5.5")
        self.assertEqual(lane["role_capability"]["write_scope"], ["orro/**", "docs/**"])
        self.assertEqual(lane["role_capability"]["tools"], {"mcp": [], "allow": []})
        self.assertEqual(lane["granted_write_scope"], ["orro/**", "docs/**"])
        self.assertEqual(lane["granted_tools"], {"mcp": [], "allow": []})
        self.assertEqual(lane["region"], [f"orro/{lane['lane_id']}.txt"])

    def test_compile_role_lane_plan_rejects_region_outside_write_scope(self) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "developer",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["shell", "codex"],
                    "write_scope": ["docs/**"],
                }
            ],
        }

        with self.assertRaises(OrroWorkflowError) as ctx:
            compile_role_lane_plan(workflow_plan=workflow_plan, rolepack=rolepack)

        self.assertEqual(ctx.exception.code, "ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION")

    def test_validate_role_lane_plan_rejects_review_only_vendor_in_execution_lane(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(workflow_plan=workflow_plan)
        role_lane_plan["lanes"][0]["adapter"] = "agy"

        with self.assertRaises(OrroWorkflowError):
            validate_role_lane_plan(role_lane_plan)

    def test_role_lane_plan_team_specs_carries_policy_model_through(self) -> None:
        import argparse

        from witnessd.__main__ import _role_lane_plan_team_specs

        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            tier="frontier",
            policy=DEFAULT_MODEL_POLICY,
        )
        args = argparse.Namespace(
            codex_binary="codex",
            claude_binary="claude",
            agy_binary="agy",
            gemini_binary="gemini",
            opencode_binary="opencode",
        )

        specs = _role_lane_plan_team_specs(role_lane_plan, args)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["adapter"], "codex")
        self.assertEqual(specs[0]["model"], "gpt-5.5")

    def test_role_lane_plan_team_specs_carries_granted_tools(self) -> None:
        import argparse

        from witnessd.__main__ import _role_lane_plan_team_specs
        from witnessd.role_capability import DEFAULT_DEVELOPER_ROLEPACK

        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            tier="frontier",
            policy=DEFAULT_MODEL_POLICY,
            rolepack=DEFAULT_DEVELOPER_ROLEPACK,
        )
        args = argparse.Namespace(
            codex_binary="codex",
            claude_binary="claude",
            agy_binary="agy",
            gemini_binary="gemini",
            opencode_binary="opencode",
        )

        specs = _role_lane_plan_team_specs(role_lane_plan, args)

        self.assertEqual(specs[0]["tools"], {"mcp": [], "allow": []})

    def test_role_lane_plan_team_specs_omits_model_when_not_policy_resolved(
        self,
    ) -> None:
        import argparse

        from witnessd.__main__ import _role_lane_plan_team_specs

        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan, lane_adapter="codex"
        )
        args = argparse.Namespace(
            codex_binary="codex",
            claude_binary="claude",
            agy_binary="agy",
            gemini_binary="gemini",
            opencode_binary="opencode",
        )

        specs = _role_lane_plan_team_specs(role_lane_plan, args)

        self.assertEqual(len(specs), 1)
        self.assertNotIn("model", specs[0])

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
                [
                    "plan without execution",
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                ]
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
