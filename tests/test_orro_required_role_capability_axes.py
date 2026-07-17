from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture
from depone.verify.adapters.base import EvidenceContext
from depone.verify.adapters.generic import read_evidence
from depone.verify.engine import run_verification

from witnessd.emitter import emit_lane_evidence
from witnessd.orro_workflow import (
    compile_role_lane_plan,
    compile_workflow_plan,
    write_role_lane_plan,
)
from witnessd.signing import gen_operator_keypair


def _runner_rolepack(
    *,
    adapter: str = "shell",
    tools: dict[str, list[str]] | None = None,
) -> dict:
    grant: dict[str, object] = {
        "role_id": "runner",
        "capability": "execute",
        "adapters": [adapter],
        "write_scope": ["pkg/**"],
    }
    if tools is not None:
        grant["tools"] = tools
    return {
        "kind": "moonweave-rolepack",
        "schema_version": "0.2",
        "name": "m14-test-runner",
        "grants": [grant],
    }


def _code_change_plan(
    *,
    adapter: str = "shell",
    tools: dict[str, list[str]] | None = None,
) -> dict:
    workflow_plan = compile_workflow_plan(
        goal="write pkg/a.py",
        profile="code-change",
    )
    return compile_role_lane_plan(
        workflow_plan=workflow_plan,
        lane_adapter=adapter,
        rolepack=_runner_rolepack(adapter=adapter, tools=tools),
    )


def _emit_bundle(
    evidence_dir: Path,
    keys_dir: Path,
    *,
    touched_file: str = "pkg/a.py",
    write_scope: list[str] | None = None,
    task_id: str = "m14-code-change",
) -> None:
    keys_dir.mkdir(parents=True, exist_ok=True)
    private_key, public_key = gen_operator_keypair(str(keys_dir))
    declared_write_scope = ["pkg/**"] if write_scope is None else write_scope
    fixture = build_reference_adapter_fixture(
        {
            "packet_version": "1.0",
            "target_harness": "shell",
            "profile": "m14-consume",
            "role": "runner",
            "toolbelt": {
                "allowed_tools": [],
                "allowed_mcp": [],
                "forbidden_tools": [],
                "context_policy": "local-code-only",
                "output_schema": "runner-result-v1",
                "evidence_obligations": ["command_receipt"],
            },
            "instructions": f"Write {touched_file} within the granted scope.",
            "evidence_obligations": ["command_receipt"],
            "context_policy": "local-code-only",
        }
    )
    emit_lane_evidence(
        {
            "command_receipts": [
                {
                    "command": ["sh", "-c", "true"],
                    "exit_code": 0,
                    "status": "passed",
                }
            ],
            "touched_files": [touched_file],
            "test_output": {"status": "passed", "summary": "1 passed"},
        },
        str(evidence_dir),
        private_key,
        fixture=fixture,
        allowed_touched_files=[touched_file],
        public_key_path=public_key,
        task_id=task_id,
        write_scope=declared_write_scope,
        role_id="runner",
        role_capability="execute",
    )


def _omit_write_scope_axis(evidence_dir: Path) -> None:
    contract_path = evidence_dir / "evidence-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["schema_version"] = "v105.verify_wedge"
    contract.pop("role_capability_write_scope")
    contract_path.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_role_capability_evidence(evidence_dir: Path) -> EvidenceContext:
    evidence = read_evidence(str(evidence_dir))
    role_capability_files = {
        "bundle.json",
        "evidence-contract.json",
        "exit-code.txt",
        "git-diff-name-only.txt",
        "run-intent.json",
    }
    evidence.files = [
        entry for entry in evidence.files if entry.path in role_capability_files
    ]
    return evidence


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class OrroRequiredRoleCapabilityAxesTests(unittest.TestCase):
    def test_code_change_plan_requires_granted_write_scope_axis(self) -> None:
        plan = _code_change_plan()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "verification-plan.json"
            write_role_lane_plan(path, plan)
            emitted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            emitted["required_role_capability_axes"],
            ["write_scope"],
        )

    def test_code_change_plan_requires_granted_tool_call_axis(self) -> None:
        plan = _code_change_plan(
            adapter="claude",
            tools={"mcp": [], "allow": ["python3"]},
        )

        self.assertEqual(
            plan["required_role_capability_axes"],
            ["write_scope", "tool_calls"],
        )

    def test_granted_tools_without_verdict_bearing_receipts_do_not_require_axis(
        self,
    ) -> None:
        plan = _code_change_plan(tools={"mcp": [], "allow": ["python3"]})

        self.assertEqual(plan["required_role_capability_axes"], ["write_scope"])

    def test_review_only_grant_does_not_require_execution_capability_axes(
        self,
    ) -> None:
        workflow_plan = compile_workflow_plan(
            goal="review the current change",
            profile="review-only",
        )
        rolepack = {
            "kind": "moonweave-rolepack",
            "schema_version": "0.2",
            "name": "m14-test-reviewer",
            "grants": [
                {
                    "role_id": "reviewer",
                    "capability": "review",
                    "adapters": ["agy"],
                    "write_scope": ["pkg/**"],
                }
            ],
        }

        plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            lane_adapter="agy",
            rolepack=rolepack,
        )

        self.assertNotIn("required_role_capability_axes", plan)

    def test_plan_required_axis_fails_closed_when_bundle_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            root = Path(tmp)
            evidence_dir = root / "evidence"
            _emit_bundle(evidence_dir, root / "keys")
            plan = _code_change_plan()

            conforming = run_verification(
                plan,
                _read_role_capability_evidence(evidence_dir),
            )
            self.assertEqual(conforming.verdict, "verified")

            _omit_write_scope_axis(evidence_dir)
            omitted = run_verification(
                plan,
                _read_role_capability_evidence(evidence_dir),
            )

        self.assertEqual(omitted.verdict, "insufficient-evidence")
        self.assertEqual(omitted.decision, "inconclusive")
        self.assertEqual(
            [
                entry.error_code
                for entry in omitted.role_capability_conformance
                if entry.axis == "write_scope"
            ],
            ["ERR_ROLE_CAPABILITY_PLAN_REQUIRED_AXIS_UNDECLARED"],
        )

    def test_docs_change_plan_requires_and_emits_write_scope_axis(self) -> None:
        workflow_plan = compile_workflow_plan(
            goal="update docs",
            profile="docs-change",
        )
        plan = compile_role_lane_plan(
            workflow_plan=workflow_plan,
            lane_adapter="shell",
        )
        lane = plan["lanes"][0]

        self.assertEqual(plan["required_role_capability_axes"], ["write_scope"])
        self.assertEqual(lane["granted_write_scope"], lane["region"])
        self.assertEqual(lane["role_capability"]["write_scope"], lane["region"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence"
            _emit_bundle(
                evidence_dir,
                root / "keys",
                touched_file=lane["region"][0],
                write_scope=lane["granted_write_scope"],
                task_id="m14-docs-change-contract",
            )
            contract = json.loads(
                (evidence_dir / "evidence-contract.json").read_text(encoding="utf-8")
            )

        self.assertEqual(
            contract["schema_version"], "v109.role_capability_write_scope"
        )
        self.assertIn("role_capability_write_scope", contract)

    def test_docs_change_write_scope_is_rederived_for_in_and_out_of_scope_writes(
        self,
    ) -> None:
        plan = compile_role_lane_plan(
            workflow_plan=compile_workflow_plan(
                goal="update docs",
                profile="docs-change",
            ),
            lane_adapter="shell",
        )
        lane = plan["lanes"][0]
        in_scope_path = lane["region"][0]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            root = Path(tmp)
            in_scope_dir = root / "in-scope"
            out_of_scope_dir = root / "out-of-scope"
            _emit_bundle(
                in_scope_dir,
                root / "keys",
                touched_file=in_scope_path,
                write_scope=lane["granted_write_scope"],
                task_id="m14-docs-change-in-scope",
            )
            _emit_bundle(
                out_of_scope_dir,
                root / "keys",
                touched_file="docs/outside-declared-region.md",
                write_scope=lane["granted_write_scope"],
                task_id="m14-docs-change-out-of-scope",
            )

            conforming = run_verification(
                plan,
                _read_role_capability_evidence(in_scope_dir),
            )
            refused = run_verification(
                plan,
                _read_role_capability_evidence(out_of_scope_dir),
            )

        self.assertEqual(conforming.verdict, "verified")
        self.assertEqual(refused.verdict, "refuted")
        self.assertEqual(
            [
                entry.error_code
                for entry in refused.role_capability_conformance
                if entry.axis == "write_scope"
            ],
            ["ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION"],
        )


if __name__ == "__main__":
    unittest.main()
