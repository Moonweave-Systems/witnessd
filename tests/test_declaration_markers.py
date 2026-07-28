from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from witnessd.claim import (
    ClaimEffect,
    ClaimEvaluation,
    ClaimFreshness,
    ClaimIntegrity,
    ClaimObservation,
    ClaimSource,
)
from witnessd.model_declaration import build_model_claim, build_model_declaration
from witnessd.orro_report import _declaration_summary, render_text_report
from witnessd.skill_routing_declaration import (
    build_skill_routing_claim,
    build_skill_routing_declaration,
)
from witnessd.tool_declaration import build_tool_claim, build_tool_declaration
from witnessd.write_scope_declaration import (
    build_write_scope_claim,
    build_write_scope_declaration,
)


EXPECTED_MEANS = "producer-reported declaration; not bundle-bound or verifier-re-derived"


class DeclarationMarkerTests(unittest.TestCase):
    def test_producer_declaration_claims_separate_the_axes(self) -> None:
        claims = [
            build_model_claim(
                adapter="agy",
                requested_model="m1",
                verification_status="requested-unverified",
            ),
            build_write_scope_claim(
                role_id="r",
                lane_id="l",
                capability="execute",
                declared_write_scope=["pkg/**"],
                allowed_touched_files=["pkg/a.py"],
                touched_files=["pkg/a.py"],
            ),
            build_skill_routing_claim(
                role_id="r",
                lane_id="l",
                capability="execute",
                skill_routing={
                    "preferred_skills": [],
                    "forbidden_skills": [],
                    "enforcement": "block",
                },
                observed_skills=[],
            ),
            build_tool_claim(
                role_id="r",
                lane_id="l",
                capability="execute",
                adapter="shell",
                declared_tools={"mcp": [], "allow": []},
            ),
        ]

        for claim in claims:
            self.assertEqual(claim.source, ClaimSource.PRODUCER)
            self.assertEqual(claim.integrity, ClaimIntegrity.UNBOUND)
            self.assertEqual(claim.evaluation, ClaimEvaluation.UNEVALUATED)
            self.assertEqual(claim.effect, ClaimEffect.ADVISORY)
        self.assertEqual(claims[0].observation, ClaimObservation.REQUESTED)
        self.assertEqual(claims[0].freshness, ClaimFreshness.PENDING)
        self.assertEqual(claims[1].observation, ClaimObservation.OBSERVED)
        self.assertEqual(claims[1].freshness, ClaimFreshness.CURRENT)
        self.assertEqual(claims[2].observation, ClaimObservation.OBSERVED)
        self.assertEqual(claims[2].freshness, ClaimFreshness.CURRENT)
        self.assertEqual(claims[3].observation, ClaimObservation.MISSING)
        self.assertEqual(claims[3].freshness, ClaimFreshness.PENDING)

    def test_legacy_payloads_remain_exact_projections(self) -> None:
        self.assertEqual(
            build_model_declaration(
                adapter="agy",
                requested_model="m1",
                verification_status="requested-unverified",
            ),
            {
                "kind": "moonweave-model-declaration",
                "schema_version": "1.0",
                "can_change_evidence_verdict": False,
                "evidence_substrate": "producer-transcribed",
                "means": EXPECTED_MEANS,
                "adapter": "agy",
                "requested_model": "m1",
                "verification_status": "requested-unverified",
                "detail": None,
            },
        )
        self.assertEqual(
            build_skill_routing_declaration(
                role_id="r",
                lane_id="l",
                capability="execute",
                skill_routing={
                    "preferred_skills": [],
                    "forbidden_skills": ["unsafe-*"],
                    "enforcement": "advisory",
                },
                observed_skills=["unsafe-write"],
            ),
            {
                "kind": "moonweave-skill-routing-declaration",
                "schema_version": "v110.role_capability_skill_routing",
                "role_id": "r",
                "lane_id": "l",
                "capability": "execute",
                "evidence_substrate": "producer-transcribed",
                "means": EXPECTED_MEANS,
                "declared_forbidden": ["unsafe-*"],
                "declared_preferred": [],
                "enforcement": "advisory",
                "observed_skills": [
                    {
                        "skill": "unsafe-write",
                        "evidence_marker": "observed raw provider event matched skill path or explicit skill declaration",
                    }
                ],
                "conformance": "advisory-fail",
                "can_change_evidence_verdict": False,
            },
        )

    def test_all_named_declarations_mark_their_verifier_boundary(self) -> None:
        declarations = [
            build_model_declaration(
                adapter="codex",
                requested_model="m1",
                verification_status="requested-unverified",
            ),
            build_write_scope_declaration(
                role_id="r",
                lane_id="l",
                capability="execute",
                declared_write_scope=["pkg/**"],
                allowed_touched_files=["pkg/a.py"],
                touched_files=["pkg/a.py"],
            ),
            build_skill_routing_declaration(
                role_id="r",
                lane_id="l",
                capability="execute",
                skill_routing={"preferred_skills": [], "forbidden_skills": []},
                observed_skills=[],
            ),
            build_tool_declaration(
                role_id="r",
                lane_id="l",
                capability="execute",
                adapter="shell",
                declared_tools={"mcp": [], "allow": []},
            ),
        ]
        for declaration in declarations:
            self.assertEqual(declaration["evidence_substrate"], "producer-transcribed")
            self.assertEqual(declaration["means"], EXPECTED_MEANS)
            self.assertFalse(declaration["can_change_evidence_verdict"])

    def test_status_payload_keeps_declaration_distinct_from_depone_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "model-declaration.json").write_text(
                json.dumps({"verification_status": "verified"}), encoding="utf-8"
            )
            declaration = _declaration_summary(run_dir)[0]
            self.assertEqual(declaration["evidence_substrate"], "producer-transcribed")
            self.assertEqual(declaration["means"], EXPECTED_MEANS)
            self.assertEqual(declaration["producer_verification_status"], "verified")
            payload = {
                "goal": "inspect",
                "summary": {"state": "needs-proofcheck"},
                "workflow": {},
                "execution": {"proofrun_evidence_present": True, "lane_count": 1},
                "verification": {
                    "proofcheck_verdict_present": True,
                    "validation_status": "validated",
                    "verifier_decision": "pass",
                    "decision": "pass",
                },
                "handoff": {},
                "human_review": {},
                "do_not_trust": [],
                "declarations": [{
                    "artifact": "model-declaration",
                    "verification_status": "verified",
                    "evidence_substrate": "producer-transcribed",
                    "means": EXPECTED_MEANS,
                    "can_change_evidence_verdict": False,
                }],
            }
            text = render_text_report(payload)
            self.assertIn("Declarations: producer-reported; not re-derived by Depone", text)
            self.assertIn("Verification: Depone proofcheck pass", text)
            self.assertNotIn("Declarations: verified", text)

    def test_report_summary_keeps_all_legacy_declaration_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            payloads = {
                "model-declaration.json": {
                    "verification_status": "requested-unverified"
                },
                "write-scope-declaration.json": {
                    "verification_status": "verified",
                    "conformance": "pass",
                },
                "skill-routing-declaration.json": {
                    "conformance": "advisory-fail"
                },
                "tool-declaration.json": {
                    "usage_verification_status": "enforced-only"
                },
            }
            for index, (name, payload) in enumerate(payloads.items()):
                lane_dir = run_dir / f"lane-{index}"
                lane_dir.mkdir()
                (lane_dir / name).write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(
                _declaration_summary(run_dir),
                [
                    {
                        "artifact": "model-declaration",
                        "path": "lane-0/model-declaration.json",
                        "evidence_substrate": "producer-transcribed",
                        "means": EXPECTED_MEANS,
                        "can_change_evidence_verdict": False,
                        "producer_verification_status": "requested-unverified",
                    },
                    {
                        "artifact": "write-scope-declaration",
                        "path": "lane-1/write-scope-declaration.json",
                        "evidence_substrate": "producer-transcribed",
                        "means": EXPECTED_MEANS,
                        "can_change_evidence_verdict": False,
                        "producer_verification_status": "verified",
                        "producer_conformance": "pass",
                    },
                    {
                        "artifact": "skill-routing-declaration",
                        "path": "lane-2/skill-routing-declaration.json",
                        "evidence_substrate": "producer-transcribed",
                        "means": EXPECTED_MEANS,
                        "can_change_evidence_verdict": False,
                        "producer_conformance": "advisory-fail",
                    },
                    {
                        "artifact": "tool-declaration",
                        "path": "lane-3/tool-declaration.json",
                        "evidence_substrate": "producer-transcribed",
                        "means": EXPECTED_MEANS,
                        "can_change_evidence_verdict": False,
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
