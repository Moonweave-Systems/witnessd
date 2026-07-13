from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


class OrroWorkstyleTests(unittest.TestCase):
    def _advise(self, goal: str, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "advise", goal, "--json", *extra])
        return code, json.loads(stdout.getvalue())

    def test_review_only_advice_does_not_recommend_proofrun(self) -> None:
        code, payload = self._advise("review this PR")

        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "orro-workstyle-decision")
        self.assertEqual(payload["task_class"], "review-only")
        self.assertEqual(payload["recommended_profile"], "review-only")
        phases = [step["phase"] for step in payload["recommended_path"]]
        self.assertNotIn("proofrun", phases)
        self.assertIn("review", " ".join(payload["rule_matches"]))

    def test_verification_only_recommends_proofcheck_not_proofrun(self) -> None:
        code, payload = self._advise("verify existing evidence")

        self.assertEqual(code, 0)
        self.assertEqual(payload["task_class"], "verification-only")
        self.assertEqual(payload["recommended_profile"], "verification-only")
        phases = [step["phase"] for step in payload["recommended_path"]]
        self.assertIn("proofcheck", phases)
        self.assertNotIn("proofrun", phases)

    def test_docs_code_risky_and_trivial_classification(self) -> None:
        cases = [
            ("update README", "docs-change", "docs-change", "bounded", False),
            ("fix parser bug", "code-change", "code-change", "bounded", True),
            ("rotate secret auth token", "risky-change", "code-change", "guarded", True),
            ("fix trivial typo", "trivial-change", "docs-change", "minimal", False),
            ("fix typo in README", "trivial-change", "docs-change", "minimal", False),
        ]
        for goal, task_class, profile, effort, human_review in cases:
            with self.subTest(goal=goal):
                code, payload = self._advise(goal)
                self.assertEqual(code, 0)
                self.assertEqual(payload["task_class"], task_class)
                self.assertEqual(payload["recommended_profile"], profile)
                self.assertEqual(payload["recommended_effort"], effort)
                self.assertEqual(payload["human_review_required"], human_review)

    def test_trivial_change_skips_unnecessary_team_execution(self) -> None:
        code, payload = self._advise("fix trivial typo")

        self.assertEqual(code, 0)
        skipped = {item["action"] for item in payload["actions_to_skip"]}
        self.assertIn("role-lane team execution", skipped)
        self.assertIn("unbounded auto", skipped)

    def test_trivial_change_recommends_direct_edit_without_scout_or_flowplan(self) -> None:
        code, payload = self._advise("fix typo in README")

        self.assertEqual(code, 0)
        phases = [step["phase"] for step in payload["recommended_path"]]
        self.assertLess(len(payload["recommended_path"]), 2)
        self.assertNotIn("scout", phases)
        self.assertNotIn("flowplan", phases)

    def test_non_trivial_code_change_keeps_full_evidence_path(self) -> None:
        code, payload = self._advise("fix parser bug")

        self.assertEqual(code, 0)
        self.assertEqual(
            [step["phase"] for step in payload["recommended_path"]],
            ["scout", "flowplan", "proofrun", "proofcheck", "handoff"],
        )

    def test_boundary_is_non_executing_non_verifying_non_assurance(self) -> None:
        code, payload = self._advise("fix parser bug")

        self.assertEqual(code, 0)
        boundary = payload["boundary"]
        self.assertFalse(boundary["executes_commands"])
        self.assertFalse(boundary["verifies_evidence"])
        self.assertFalse(boundary["approves_merge"])
        self.assertFalse(boundary["raises_assurance"])
        self.assertTrue(boundary["depone_verifies"])
        self.assertTrue(boundary["witnessd_executes"])
        self.assertTrue(boundary["orro_exposes_workflow"])

    def test_out_writes_same_json_without_creating_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "workstyle.json"
            code, payload = self._advise(
                "fix parser bug",
                "--repo",
                str(root),
                "--home",
                str(root / ".witnessd"),
                "--out",
                str(out),
            )

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self.assertFalse((root / "proofcheck-verdict.json").exists())
            self.assertFalse((root / "orro-handoff.json").exists())

    def test_missing_goal_fails_closed(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "advise", "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_ADVISE_INPUT_REQUIRED")

    def test_orro_module_and_witnessd_orro_alias_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        depone_root = root.parent / "depone"
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(depone_root) if not current_pythonpath else f"{depone_root}{os.pathsep}{current_pythonpath}"
        )

        module = subprocess.run(
            [sys.executable, "-m", "orro", "advise", "verify existing evidence", "--repo", ".", "--json"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        alias = subprocess.run(
            [
                sys.executable,
                "-m",
                "witnessd",
                "orro",
                "advise",
                "verify existing evidence",
                "--repo",
                ".",
                "--json",
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertIn("deprecated", module.stderr)
        self.assertIn("ORRO package", module.stderr)
        self.assertEqual(alias.stderr, "")
        module_payload = json.loads(module.stdout)
        alias_payload = json.loads(alias.stdout)
        self.assertEqual(module_payload["task_class"], "verification-only")
        self.assertEqual(alias_payload["task_class"], "verification-only")
        self.assertEqual(module_payload["recommended_path"], alias_payload["recommended_path"])

    def test_orro_help_includes_advise_without_engine_internal_commands(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "orro", "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deprecated", result.stderr)
        self.assertIn("ORRO package", result.stderr)
        self.assertIn("advise", result.stdout)
        self.assertNotIn("self-test", result.stdout)


if __name__ == "__main__":
    unittest.main()
