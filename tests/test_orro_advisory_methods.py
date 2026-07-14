from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from orro.__main__ import main as orro_main
from witnessd.__main__ import main
from witnessd.orro_advisory import build_sketch_decision, build_trace_decision


BOUNDARY_FLAGS = (
    "raises_assurance",
    "verifies_evidence",
    "can_change_evidence_verdict",
    "executes_proofrun",
)


def _write_reproduction_receipt(
    repo: Path,
    *,
    symptom: str,
    output: str,
    exit_code: int = 1,
    external_confirmation: dict | None = None,
) -> None:
    (repo / "orro-trace-reproduction.json").write_text(
        json.dumps(
            {
                "kind": "orro-trace-reproduction",
                "symptom": symptom,
                "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
                "exit_code": exit_code,
                "stdout": "",
                "stderr": output,
                "minimized": True,
                "external_confirmation": external_confirmation,
            }
        )
        + "\n",
        encoding="utf-8",
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

    def test_sketch_emits_researched_controlled_convergence_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")

            payload = build_sketch_decision(
                "add a health endpoint without replacing the current API",
                repo=repo,
            )

            criteria = payload.get("criteria", [])
            candidates = payload.get("candidates", [])
            rejected = payload.get("rejected", [])
            researched_shape_present = all(
                (
                    set(payload.get("frame", {})) >= {"outcome", "why", "success_signal"},
                    3 <= len(criteria) <= 6,
                    all({"name", "weight", "repo_signal"} <= set(item) for item in criteria),
                    len(candidates) >= 3,
                    len({item.get("axis") for item in candidates}) == len(candidates),
                    all(item.get("per_criterion_scores") for item in candidates),
                    bool(payload.get("devils_advocate")),
                    set(payload.get("chosen", {}))
                    >= {"reason", "confidence", "what_would_change_it"},
                    len(rejected) == len(candidates) - 1,
                    all({"option", "why_lost"} <= set(item) for item in rejected),
                    bool(payload.get("riskiest_assumption", {}).get("spike_or_tracer")),
                    bool(payload.get("no_gos")),
                    bool(payload.get("rabbit_holes")),
                    set(payload.get("decision_record", {}))
                    >= {"context", "decision", "consequences"},
                )
            )
            self.assertTrue(researched_shape_present)

    def test_sketch_scores_change_with_materially_different_repo_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seam_repo = root / "with-seam"
            seam_repo.mkdir()
            (seam_repo / "AGENTS.md").write_text("# Existing advisory seam\n", encoding="utf-8")
            isolated_repo = root / "without-seam"
            isolated_repo.mkdir()
            for index in range(20):
                (isolated_repo / f"module_{index}.py").write_text(
                    f"VALUE = {index}\n",
                    encoding="utf-8",
                )

            seam = build_sketch_decision("extend current advisory behavior", repo=seam_repo)
            isolated = build_sketch_decision(
                "isolate a new boundary behind the current entrypoint",
                repo=isolated_repo,
            )
            parallel = build_sketch_decision(
                "create a new subsystem with a separate lifecycle",
                repo=seam_repo,
            )

            responsive_scoring = all(
                (
                    seam["chosen"]["option"] == "bounded-existing-seam",
                    isolated["chosen"]["option"] == "isolated-module-adapter",
                    parallel["chosen"]["option"] == "new-parallel-subsystem",
                    seam["candidates"] != isolated["candidates"],
                )
            )
            self.assertTrue(responsive_scoring)

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
            self.assertIn("unconfirmed", payload)
            self.assertNotIn("root_cause", payload)
            self.assertFalse(payload["recommended_fix_scope"]["fix_proposal_allowed"])
            self.assertEqual(payload["recommended_fix_scope"]["allowed_paths"], [])
            self.assertFalse((root / ".witnessd" / "runs").exists())
            self._assert_advisory_boundary(payload)

    def test_trace_emits_researched_falsification_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "service.py").write_text(
                "def resolve_endpoint() -> str:\n    return '/wrong'\n",
                encoding="utf-8",
            )
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_service.py").write_text(
                "import unittest\n"
                "from service import resolve_endpoint\n\n"
                "class ServiceTests(unittest.TestCase):\n"
                "    def test_health_endpoint(self) -> None:\n"
                "        self.assertEqual(resolve_endpoint(), '/health')\n",
                encoding="utf-8",
            )
            symptom = "service.py resolve_endpoint returns /wrong instead of /health"
            _write_reproduction_receipt(
                repo,
                symptom=symptom,
                output=(
                    "FAIL: test_health_endpoint (tests.test_service.ServiceTests)\n"
                    "AssertionError: '/wrong' != '/health'"
                ),
            )

            payload = build_trace_decision(
                symptom,
                repo=repo,
            )

            hypotheses = payload.get("hypotheses", [])
            researched_shape_present = all(
                (
                    bool(payload.get("check_the_plug")),
                    set(payload.get("reproduction", {}))
                    >= {"steps", "minimized", "red_observed"},
                    set(payload.get("localization", {}))
                    >= {"technique", "suspect_region_cited"},
                    len(hypotheses) >= 2,
                    all(
                        {"mechanism", "prediction", "discriminating_probe", "confidence"}
                        <= set(item)
                        for item in hypotheses
                    ),
                    set(payload.get("confirmation", {}))
                    >= {"lint_ran", "lint_only", "can_confirm"},
                    payload.get("confirmation", {}).get("lint_only") is True,
                    payload.get("confirmation", {}).get("can_confirm") is False,
                    isinstance(payload.get("logbook"), list),
                    "root_cause" in payload,
                    set(payload.get("fix_scope", {}))
                    >= {"cause_site", "blast_radius", "invariant", "regression_test"},
                )
            )
            self.assertTrue(researched_shape_present)

    def test_trace_hard_gate_cannot_confirm_without_observed_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")

            payload = build_trace_decision("app.py returns the wrong value", repo=repo)

            hard_gate_held = all(
                (
                    payload["reproduction"].get("red_observed") is not True,
                    "unconfirmed" in payload,
                    "root_cause" not in payload,
                    payload.get("hypotheses") == [],
                    payload["flowplan_handoff"]["status"] == "blocked-root-cause-unconfirmed",
                )
            )
            self.assertTrue(hard_gate_held)

    def test_trace_consumes_actual_run_receipt_without_executing_or_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "calculator.py").write_text(
                "def add(left: int, right: int) -> int:\n    return left - right\n",
                encoding="utf-8",
            )
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_calculator.py").write_text(
                "import unittest\n"
                "from calculator import add\n\n"
                "class CalculatorTests(unittest.TestCase):\n"
                "    def test_add(self) -> None:\n"
                "        self.assertEqual(add(2, 1), 3)\n",
                encoding="utf-8",
            )
            symptom = "calculator.py add returns 1 instead of 3"
            _write_reproduction_receipt(
                repo,
                symptom=symptom,
                output=(
                    "FAIL: test_add (tests.test_calculator.CalculatorTests)\n"
                    "AssertionError: 1 != 3"
                ),
            )
            before = {
                path.relative_to(repo).as_posix(): path.read_bytes()
                for path in repo.rglob("*")
                if path.is_file()
            }

            payload = build_trace_decision(
                symptom,
                repo=repo,
            )

            after = {
                path.relative_to(repo).as_posix(): path.read_bytes()
                for path in repo.rglob("*")
                if path.is_file()
            }
            isolated_red_and_probe = all(
                (
                    before == after,
                    payload.get("reproduction", {}).get("red_observed") is True,
                    payload.get("confirmation", {}).get("lint_ran") is True,
                    any(item.get("result") for item in payload.get("logbook", [])),
                    payload.get("root_cause", {}).get("tier")
                    in {"confirmed", "suspected", "speculative"},
                    payload["reproduction"].get("source") == "orro-trace-reproduction.json",
                    payload["boundary"]["mutates_repo"] is False,
                    payload["boundary"]["executes_commands"] is False,
                    payload["boundary"]["executes_proofrun"] is False,
                    next(
                        item for item in payload["logbook"] if item["hypothesis"] == "H3"
                    )["outcome"]
                    == "falsified",
                )
            )
            self.assertTrue(isolated_red_and_probe)

    def test_trace_does_not_use_an_unrelated_failing_test_as_the_symptom_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def health() -> str:\n    return 'ok'\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_unrelated.py").write_text(
                "import unittest\n\n"
                "class UnrelatedTests(unittest.TestCase):\n"
                "    def test_math(self) -> None:\n"
                "        self.assertEqual(1, 2)\n",
                encoding="utf-8",
            )
            _write_reproduction_receipt(
                repo,
                symptom="unrelated math failure",
                output=(
                    "FAIL: test_math (tests.test_unrelated.UnrelatedTests)\n"
                    "AssertionError: 1 != 2"
                ),
            )

            payload = build_trace_decision("app.py health returns down", repo=repo)

            symptom_gate_held = all(
                (
                    payload["reproduction"].get("suite_red_observed") is True,
                    payload["reproduction"].get("red_observed") is False,
                    payload["hypotheses"] == [],
                    "unconfirmed" in payload,
                )
            )
            self.assertTrue(symptom_gate_held)

    def test_trace_ranks_the_hypothesis_supported_by_the_discriminating_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "service.py").write_text(
                "import missing_dependency\n\n"
                "def resolve_endpoint() -> str:\n"
                "    return '/health'\n",
                encoding="utf-8",
            )
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_service.py").write_text(
                "import unittest\n"
                "from service import resolve_endpoint\n\n"
                "class ServiceTests(unittest.TestCase):\n"
                "    def test_resolve_endpoint(self) -> None:\n"
                "        self.assertEqual(resolve_endpoint(), '/health')\n",
                encoding="utf-8",
            )
            symptom = "service.py resolve_endpoint fails during import"
            _write_reproduction_receipt(
                repo,
                symptom=symptom,
                output=(
                    "ERROR: test_service (unittest.loader._FailedTest.test_service)\n"
                    "ModuleNotFoundError: No module named 'missing_dependency'"
                ),
            )

            payload = build_trace_decision(
                symptom,
                repo=repo,
            )

            probe_drives_verdict = all(
                (
                    payload["reproduction"].get("red_observed") is True,
                    payload["logbook"][0]["hypothesis"] == "H2",
                    payload["logbook"][0]["outcome"] == "survives",
                    payload["root_cause"]["finding"]
                    == "effective configuration or environment changes the runtime behavior",
                )
            )
            self.assertTrue(probe_drives_verdict)

    def test_degraded_trace_does_not_promote_heuristic_hypotheses_to_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "calculator.py").write_text(
                "def add(left: int, right: int) -> int:\n    return left - right\n",
                encoding="utf-8",
            )
            symptom = "calculator.py add returns 1 instead of 3"
            _write_reproduction_receipt(
                repo,
                symptom=symptom,
                output="FAIL: test_add\nAssertionError: 1 != 3",
                external_confirmation={
                    "discriminating_probe_ran": True,
                    "ruled_out_rival": True,
                    "red_to_green_observed": True,
                    "reported_verbatim": "operator reran test_add after isolating add: PASS",
                },
            )

            payload = build_trace_decision(symptom, repo=repo)

            self.assertEqual(payload["root_cause"]["tier"], "suspected")
            self.assertEqual(
                payload["flowplan_handoff"]["status"],
                "blocked-root-cause-unconfirmed",
            )
            self.assertFalse(payload["recommended_fix_scope"]["fix_proposal_allowed"])

    def test_skillpacks_cite_researched_methods_and_external_signal_rule(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sketch = (root / "orro" / "skillpacks" / "sketch.md").read_text(encoding="utf-8")
        trace = (root / "orro" / "skillpacks" / "trace.md").read_text(encoding="utf-8")
        required_sketch_sources = (
            "designcouncil.org.uk/resources/the-double-diamond",
            "workingbackwards.com",
            "basecamp.com/shapeup/1.1-chapter-02",
            "github.com/rust-lang/rfcs",
            "dspace.mit.edu/handle/1721.1/49448",
            "cognitect.com/blog/2011/11/15/documenting-architecture-decisions",
            "arxiv.org/abs/2203.11171",
            "arxiv.org/abs/2305.10601",
            "arxiv.org/abs/2308.09687",
        )
        required_trace_sources = (
            "queue.acm.org/detail.cfm?id=1217270",
            "git-scm.com/docs/git-bisect",
            "debuggingrules.com",
            "arxiv.org/abs/2309.11495",
            "arxiv.org/abs/2210.03629",
            "arxiv.org/abs/2303.11366",
            "arxiv.org/abs/2407.01489",
            "arxiv.org/abs/2404.05427",
        )
        researched_skillpacks_present = all(
            (
                all(url in sketch for url in required_sketch_sources),
                all(url in trace for url in required_trace_sources),
                "stated confidence is not evidence" in sketch.lower(),
                "stated confidence is not evidence" in trace.lower(),
                "arxiv.org/abs/2310.01798" in sketch,
                "arxiv.org/abs/2310.01798" in trace,
                "reference knowledge" in sketch.lower(),
                "reference knowledge" in trace.lower(),
                "--decision" in sketch,
                "--decision" in trace,
                "does not author" in sketch.lower(),
                "does not author" in trace.lower(),
            )
        )
        self.assertTrue(researched_skillpacks_present)

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

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("Depone v110", stdout.getvalue())

    def test_product_help_keeps_scout_in_the_public_pipeline(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = orro_main(["--help"])

        self.assertEqual(code, 0)
        help_text = stdout.getvalue()
        self.assertIn("scout -> sketch/trace -> flowplan", help_text)
        self.assertIn("Depone v110", help_text)

    def test_trace_without_localization_does_not_invent_a_code_path_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")

            payload = build_trace_decision(
                "unrelated symptom vocabulary",
                repo=repo,
            )

            self.assertEqual(payload["hypotheses"], [])
            self.assertIn("unconfirmed", payload)
            self.assertIn("observed red", payload["unconfirmed"]["missing_evidence"])

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
