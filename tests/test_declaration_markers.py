from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from witnessd.model_declaration import build_model_declaration
from witnessd.orro_report import _declaration_summary, render_text_report
from witnessd.skill_routing_declaration import build_skill_routing_declaration
from witnessd.tool_declaration import build_tool_declaration
from witnessd.write_scope_declaration import build_write_scope_declaration


EXPECTED_MEANS = "producer-reported declaration; not bundle-bound or verifier-re-derived"


class DeclarationMarkerTests(unittest.TestCase):
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
                "verification": {"proofcheck_verdict_present": True, "decision": "pass"},
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


if __name__ == "__main__":
    unittest.main()
