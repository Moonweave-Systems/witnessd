from __future__ import annotations

import io
import json
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.model_policy import DEFAULT_MODEL_POLICY
from witnessd.orro_workflow import (
    REVIEW_ONLY_ADAPTERS,
    OrroWorkflowError,
    assert_workflow_phase_allowed,
    compile_role_lane_plan,
    compile_workflow_plan,
    validate_role_lane_plan,
)


def _runner_rolepack(
    *,
    adapters: list[str],
    write_scope: list[str],
    model: str | None = None,
    name: str = "developer",
) -> dict:
    grant = {
        "role_id": "runner",
        "capability": "execute",
        "adapters": adapters,
        "write_scope": write_scope,
    }
    if model is not None:
        grant["model"] = model
    return {
        "kind": "moonweave-rolepack",
        "schema_version": "0.2",
        "name": name,
        "grants": [grant],
    }


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

    def test_critic_only_profile_compiles_one_dedicated_claude_lane(self) -> None:
        plan = compile_workflow_plan(
            goal="criticize the current changes", profile="critic-only"
        )
        role_lanes = compile_role_lane_plan(
            workflow_plan=plan,
            lane_adapter="agy",
        )

        self.assertEqual(REVIEW_ONLY_ADAPTERS, ("agy", "gemini"))
        self.assertEqual(plan["profile"], "critic-only")
        self.assertNotIn("proofrun", plan["flow"])
        self.assertFalse(role_lanes["execution_allowed"])
        self.assertEqual(len(role_lanes["lanes"]), 1)
        lane = role_lanes["lanes"][0]
        self.assertEqual(lane["role_id"], "critic")
        self.assertEqual(lane["adapter"], "claude")
        self.assertEqual(lane["phase"], "review")
        self.assertEqual(lane["critic_contract"], "claude-critic-v2.1")
        self.assertFalse(lane["may_execute"])
        self.assertFalse(lane["may_verify"])
        self.assertFalse(lane["raises_assurance"])

    def test_critic_only_profile_forbids_proofrun(self) -> None:
        plan = compile_workflow_plan(
            goal="criticize the current changes", profile="critic-only"
        )

        with self.assertRaises(OrroWorkflowError) as cm:
            assert_workflow_phase_allowed(plan, "proofrun")

        self.assertEqual(cm.exception.code, "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")

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
                    "--rolepack",
                    "developer",
                    "--model-policy",
                    "default",
                    "--json",
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
            self.assertEqual(role_lanes["lane_count"], 1)
            self.assertEqual(role_lanes["distinct_adapter_count"], 1)
            self.assertEqual(role_lanes["distinct_model_count"], 1)
            self.assertFalse(role_lanes["multi_model_execution"])
            self.assertEqual(payload["lane_count"], 1)
            self.assertEqual(payload["distinct_adapter_count"], 1)
            self.assertEqual(payload["distinct_model_count"], 1)
            self.assertFalse(payload["multi_model_execution"])
            self.assertEqual(payload["role_lane_plan"]["lane_count"], 1)
            self.assertEqual(
                payload["role_lane_plan"]["distinct_adapter_count"], 1
            )
            self.assertEqual(payload["role_lane_plan"]["distinct_model_count"], 1)
            self.assertFalse(payload["role_lane_plan"]["multi_model_execution"])
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
                self.assertEqual(lane["adapter"], "codex")
                self.assertEqual(lane["region"], ["orro/**", "docs/**"])
                self.assertTrue(lane["may_execute"])
                self.assertFalse(lane["may_verify"])
                self.assertFalse(lane["raises_assurance"])
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self.assertFalse((root / "team-ledger.json").exists())

    def test_compile_role_lane_plan_labels_distinct_multi_model_execution(self) -> None:
        workflow_plan = compile_workflow_plan(
            goal="implement and review parser fix", profile="code-change"
        )
        runner = next(
            role for role in workflow_plan["roles"] if role["role_id"] == "runner"
        )
        reviewer = deepcopy(runner)
        reviewer.update(
            {
                "role_id": "reviewer",
                "purpose": "review the parser implementation",
            }
        )
        workflow_plan["roles"].append(reviewer)
        policy = {
            "kind": DEFAULT_MODEL_POLICY["kind"],
            "schema_version": DEFAULT_MODEL_POLICY["schema_version"],
            "routes": [
                {
                    "role_kind": "runner",
                    "tier": "quick",
                    "budget": {"max_tokens": 1, "max_usd": 1.0, "max_depth": 1},
                    "candidates": [{"adapter": "codex", "model": "model-a"}],
                },
                {
                    "role_kind": "reviewer",
                    "tier": "quick",
                    "budget": {"max_tokens": 1, "max_usd": 1.0, "max_depth": 1},
                    "candidates": [{"adapter": "claude", "model": "model-b"}],
                },
            ],
        }
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "two-lane-test",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "write_scope": ["src/**"],
                },
                {
                    "role_id": "reviewer",
                    "capability": "execute",
                    "adapters": ["claude"],
                    "write_scope": ["tests/**"],
                },
            ],
        }

        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            policy=policy,
            rolepack=rolepack,
        )

        self.assertEqual(role_lane_plan["lane_count"], 2)
        self.assertEqual(role_lane_plan["distinct_adapter_count"], 2)
        self.assertEqual(role_lane_plan["distinct_model_count"], 2)
        self.assertTrue(role_lane_plan["multi_model_execution"])

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
            for lane in role_lanes["lanes"]:
                self.assertEqual(lane["granted_write_scope"], lane["region"])
                self.assertEqual(
                    lane["role_capability"]["write_scope"], lane["region"]
                )
                self.assertTrue(
                    all(path.startswith("docs/") for path in lane["region"])
                )
            self.assertEqual(
                role_lanes["required_role_capability_axes"], ["write_scope"]
            )
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

    def test_flowplan_role_lanes_without_write_scope_fails_closed(self) -> None:
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
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            payload = json.loads(text)
            error = payload["error"]
            self.assertEqual(error["code"], "ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED")
            self.assertEqual(
                error["reason"],
                "code-change proofrun lanes need a concrete write_scope from the rolepack",
            )
            self.assertEqual(
                error["required_input_or_grant"],
                "a rolepack granting the role's write_scope",
            )
            self.assertIn("team init --template developer", error["next_command"])
            self.assertIn("--write-scope '<glob>'", error["next_command"])
            self.assertIn("--model-policy default", error["next_command"])

    def test_flowplan_adapter_not_granted_has_actionable_rolepack_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "role-lane-plan.json"
            rolepack_path = root / "rolepack.json"
            rolepack_path.write_text(
                json.dumps(
                    _runner_rolepack(
                        adapters=["codex"],
                        write_scope=["src/**"],
                    )
                )
                + "\n",
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
                    str(out),
                    "--rolepack-file",
                    str(rolepack_path),
                    "--model-policy",
                    "off",
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            error = json.loads(text)["error"]
            self.assertEqual(error["code"], "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED")
            self.assertIn("resolved adapter 'shell'", error["reason"])
            self.assertIn("granted adapters ['codex']", error["reason"])
            self.assertIn("grants role_ids ['runner']", error["reason"])
            self.assertIn(
                "pass --model-policy default (routes to the granted adapter)",
                error["reason"],
            )
            self.assertIn("adapter 'shell'", error["required_input_or_grant"])
            self.assertIn("--model-policy default", error["next_command"])

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
            self.assertEqual(lane["model"], "gpt-5.6-sol")
            self.assertEqual(lane["model_source"], "model-policy")
            self.assertEqual(lane["granted_adapters"], ["codex"])
            self.assertEqual(
                lane["budget"],
                {"max_tokens": 1000000, "max_usd": 6.0, "max_depth": 1},
            )
            self.assertEqual(lane["granted_write_scope"], ["orro/**", "docs/**"])
            self.assertEqual(lane["region"], ["orro/**", "docs/**"])
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
                        "--model-policy",
                        "default",
                    ]
                )

            self.assertEqual(code, 0)
            lane = json.loads(out.read_text(encoding="utf-8"))["lanes"][0]
            self.assertEqual(lane["role_capability"]["role_id"], "runner")

    def test_flowplan_role_lanes_model_policy_default_resolves_runner_to_codex(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "role-lane-plan.json"
            rolepack_path = root / "rolepack.json"
            rolepack_path.write_text(
                json.dumps(
                    _runner_rolepack(
                        adapters=["shell", "codex"], write_scope=["orro/**"]
                    )
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
                    "--model-policy",
                    "default",
                    "--role-lane-tier",
                    "frontier",
                    "--rolepack-file",
                    str(rolepack_path),
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
            self.assertEqual(lane["model"], "gpt-5.6-sol")
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
            self.assertEqual(lane["model"], "gemini-3.5-flash")
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
                    "budget": {"max_tokens": 1, "max_usd": 1.0, "max_depth": 1},
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
                    "write_scope": ["orro/**"],
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
        self.assertEqual(lane["model"], "gpt-5.6-sol")
        self.assertEqual(lane["model_source"], "model-policy")
        self.assertEqual(lane["granted_adapters"], ["codex"])
        self.assertEqual(lane["role_capability"]["role_id"], "runner")
        self.assertEqual(lane["role_capability"]["capability"], "execute")
        self.assertNotIn("model", lane["role_capability"])
        self.assertEqual(lane["role_capability"]["write_scope"], ["orro/**", "docs/**"])
        self.assertEqual(lane["role_capability"]["tools"], {"mcp": [], "allow": []})
        self.assertEqual(lane["granted_write_scope"], ["orro/**", "docs/**"])
        self.assertEqual(lane["granted_tools"], {"mcp": [], "allow": []})
        self.assertEqual(lane["region"], ["orro/**", "docs/**"])

    def test_compile_role_lane_plan_requires_code_change_write_scope(self) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "scope-missing",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model": "gpt-5.5",
                }
            ],
        }

        with self.assertRaises(OrroWorkflowError) as ctx:
            compile_role_lane_plan(workflow_plan=workflow_plan, rolepack=rolepack)

        self.assertEqual(ctx.exception.code, "ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED")

    def test_role_lane_plan_team_specs_preserves_glob_write_scope_region(self) -> None:
        import argparse

        from witnessd.__main__ import _role_lane_plan_team_specs

        workflow_plan = compile_workflow_plan(goal="fix frontend", profile="code-change")
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "frontend",
            "grants": [
                {
                    "role_id": "runner",
                    "capability": "execute",
                    "adapters": ["codex"],
                    "model": "gpt-5.5",
                    "write_scope": ["frontend/**", "src/**"],
                }
            ],
        }
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            tier="frontier",
            rolepack=rolepack,
        )
        args = argparse.Namespace(
            codex_binary="codex",
            claude_binary="claude",
            agy_binary="agy",
            gemini_binary="gemini",
            opencode_binary="opencode",
        )

        lane = role_lane_plan["lanes"][0]
        self.assertEqual(lane["region"], ["frontend/**", "src/**"])
        self.assertEqual(lane["granted_write_scope"], ["frontend/**", "src/**"])
        self.assertNotEqual(lane["region"], [f"orro/{lane['lane_id']}.txt"])

        specs = _role_lane_plan_team_specs(role_lane_plan, args)

        self.assertEqual(specs[0]["region"], ["frontend/**", "src/**"])
        self.assertEqual(specs[0]["allowed_touched_files"], ["frontend/**", "src/**"])
        self.assertEqual(specs[0]["write_scope"], ["frontend/**", "src/**"])

    def test_compile_role_lane_plan_preserves_docs_only_code_change_write_scope(self) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        rolepack = _runner_rolepack(adapters=["shell", "codex"], write_scope=["docs/**"])

        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan, rolepack=rolepack
        )

        self.assertEqual(role_lane_plan["lanes"][0]["region"], ["docs/**"])

    def test_validate_role_lane_plan_rejects_review_only_vendor_in_execution_lane(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            lane_adapter="codex",
            rolepack=_runner_rolepack(adapters=["codex"], write_scope=["orro/**"]),
        )
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
            rolepack=_runner_rolepack(
                adapters=["shell", "codex"], write_scope=["orro/**"]
            ),
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
        self.assertEqual(specs[0]["model"], "gpt-5.6-sol")

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
            workflow_plan=workflow_plan,
            lane_adapter="codex",
            rolepack=_runner_rolepack(adapters=["codex"], write_scope=["orro/**"]),
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
