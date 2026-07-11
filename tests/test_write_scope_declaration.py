from __future__ import annotations

import unittest

from witnessd.write_scope_declaration import build_write_scope_declaration


class WriteScopeDeclarationTests(unittest.TestCase):
    def test_records_verified_pass_when_touched_files_are_within_scope(self) -> None:
        declaration = build_write_scope_declaration(
            role_id="runner",
            lane_id="lane-a",
            capability="execute",
            declared_write_scope=["pkg/**"],
            allowed_touched_files=["pkg/a.py"],
            touched_files=["pkg/a.py"],
        )

        self.assertEqual(declaration["kind"], "moonweave-write-scope-declaration")
        self.assertFalse(declaration["can_change_evidence_verdict"])
        self.assertEqual(declaration["verification_status"], "verified")
        self.assertEqual(declaration["conformance"], "pass")
        self.assertIsNone(declaration["detail"])

    def test_records_rejected_fail_when_touched_files_escape_scope(self) -> None:
        declaration = build_write_scope_declaration(
            role_id="runner",
            lane_id="lane-a",
            capability="execute",
            declared_write_scope=["pkg/**"],
            allowed_touched_files=["pkg/a.py", "secrets.txt"],
            touched_files=["pkg/a.py", "secrets.txt"],
        )

        self.assertEqual(declaration["verification_status"], "rejected")
        self.assertEqual(declaration["conformance"], "fail")
        self.assertEqual(
            declaration["detail"],
            "touched_files are not a subset of declared_write_scope",
        )


if __name__ == "__main__":
    unittest.main()
