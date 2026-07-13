from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from orro.__main__ import main as orro_main
from witnessd.__main__ import main
from witnessd.orro_advisory import build_trace_decision


BOUNDARY_FLAGS = (
    "raises_assurance",
    "verifies_evidence",
    "can_change_evidence_verdict",
    "executes_proofrun",
)


class OrroAdvisoryMethodTests(unittest.TestCase):
    def _run(self, mode: str, goal: str, *, repo: Path, home: Path, out: Path) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "orro",
                    mode,
                    goal,
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                    "--out",
                    str(out),
                    "--json",
                ]
            )
        return code, json.loads(stdout.getvalue())

    def _assert_advisory_boundary(self, payload: dict) -> None:
        boundary = payload["boundary"]
        for flag in BOUNDARY_FLAGS:
            with self.subTest(flag=flag):
                self.assertIn(flag, boundary)
                self.assertFalse(boundary[flag])
        self.assertFalse(boundary["is_evidence"])
        self.assertFalse(boundary["approves_merge"])

    def test_sketch_emits_converged_flowplan_input_with_resolved_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "sample"
            repo.mkdir()
            (repo / "app.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
            out = root / "orro-sketch-decision.json"

            code, payload = self._run(
                "sketch",
                "add a health endpoint without changing the public API",
                repo=repo,
                home=root / ".witnessd",
                out=out,
            )

            self.assertEqual(code, 0)
            self.assertEqual(payload["kind"], "orro-sketch")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)
            self.assertTrue(payload["problem_frame"]["desired_outcome"])
            self.assertGreaterEqual(len(payload["candidate_approaches"]), 3)
            self.assertTrue(all(item["tradeoffs"] for item in payload["candidate_approaches"]))
            chosen = payload["chosen_direction"]
            self.assertTrue(chosen["summary"])
            self.assertTrue(chosen["rationale"])
            self.assertEqual(chosen["flowplan_input"], payload["flowplan_handoff"]["goal"])
            self.assertEqual(payload["flowplan_handoff"]["profile"], "code-change")
            self.assertEqual(payload["flowplan_handoff"]["command"][1], "flowplan")
            self.assertTrue(payload["decision_branches"])
            self.assertTrue(
                all(branch["recommended_answer"] for branch in payload["decision_branches"])
            )
            self._assert_advisory_boundary(payload)

    def test_trace_is_read_only_and_blocks_fix_before_root_cause_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "sample"
            repo.mkdir()
            (repo / "service.py").write_text(
                "def load_config(path: str) -> str:\n    return path\n",
                encoding="utf-8",
            )
            before = {
                path.relative_to(repo).as_posix(): path.read_bytes()
                for path in repo.rglob("*")
                if path.is_file()
            }
            out = root / "orro-trace-decision.json"

            code, payload = self._run(
                "trace",
                "service.py load_config returns the wrong path",
                repo=repo,
                home=root / ".witnessd",
                out=out,
            )

            after = {
                path.relative_to(repo).as_posix(): path.read_bytes()
                for path in repo.rglob("*")
                if path.is_file()
            }
            self.assertEqual(code, 0)
            self.assertEqual(payload["kind"], "orro-trace")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)
            self.assertEqual(before, after)
            self.assertEqual(
                [phase["name"] for phase in payload["investigation_phases"]],
                ["observe", "reproduce-localize", "hypothesize", "confirm-root-cause"],
            )
            self.assertTrue(payload["symptom"])
            self.assertIn("status", payload["reproduction"])
            self.assertTrue(payload["evidence_gathered"])
            self.assertEqual(
                [item["rank"] for item in payload["ranked_hypotheses"]],
                list(range(1, len(payload["ranked_hypotheses"]) + 1)),
            )
            self.assertIn(payload["root_cause"]["status"], {"confirmed", "unconfirmed"})
            if payload["root_cause"]["status"] == "unconfirmed":
                self.assertFalse(payload["recommended_fix_scope"]["fix_proposal_allowed"])
                self.assertEqual(payload["recommended_fix_scope"]["allowed_paths"], [])
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self._assert_advisory_boundary(payload)

    def test_public_help_lists_both_advisory_surfaces(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["orro", "sketch", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("advisory", stdout.getvalue().lower())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["orro", "trace", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("root cause", stdout.getvalue().lower())

    def test_product_help_keeps_scout_in_the_public_pipeline(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = orro_main(["--help"])

        self.assertEqual(code, 0)
        help_text = stdout.getvalue()
        self.assertIn("scout -> sketch/trace -> flowplan", help_text)

    def test_trace_without_localization_does_not_invent_a_code_path_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")

            payload = build_trace_decision(
                "unrelated symptom vocabulary",
                repo=repo,
            )

            first = payload["ranked_hypotheses"][0]
            self.assertNotIn("localized implementation path", first["hypothesis"])
            self.assertEqual(first["status"], "localization-required")

    def test_advisory_output_cannot_mutate_the_inspected_repo(self) -> None:
        for mode in ("sketch", "trace"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "sample"
                repo.mkdir()
                source = repo / "app.py"
                source.write_text("def main() -> None:\n    pass\n", encoding="utf-8")
                before = source.read_bytes()
                out = repo / f"orro-{mode}-decision.json"

                code, payload = self._run(
                    mode,
                    "inspect without changing the repository",
                    repo=repo,
                    home=root / ".witnessd",
                    out=out,
                )

                self.assertEqual(code, 1)
                self.assertEqual(
                    payload["error"]["code"],
                    "ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO",
                )
                self.assertFalse(out.exists())
                self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
