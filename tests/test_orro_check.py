from __future__ import annotations

import io
import json
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _run(argv: list[str]) -> tuple[int, object, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    stdout = out.getvalue()
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_raw": stdout}
    return code, payload, err.getvalue()


def _fake_agy(directory: Path) -> str:
    path = directory / "agy"
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "capture = os.environ.get('AGY_ARGV_CAPTURE')\n"
        "if capture:\n"
        "    pathlib.Path(capture).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n"
        "cache_capture = os.environ.get('ORRO_CACHE_CAPTURE')\n"
        "if cache_capture:\n"
        "    pathlib.Path(cache_capture).write_text(os.environ['PYTHONPYCACHEPREFIX'] + '\\n' + os.environ['RUFF_CACHE_DIR'] + '\\n', encoding='utf-8')\n"
        "if os.environ.get('AGY_WRITE_CACHE') == '1':\n"
        "    pathlib.Path(os.environ['RUFF_CACHE_DIR']).mkdir(parents=True, exist_ok=True)\n"
        "    pathlib.Path(os.environ['RUFF_CACHE_DIR'], 'cache.bin').write_text('cache', encoding='utf-8')\n"
        "    pycache = pathlib.Path(os.environ['PYTHONPYCACHEPREFIX'], 'pkg')\n"
        "    pycache.mkdir(parents=True, exist_ok=True)\n"
        "    pathlib.Path(pycache, 'mod.pyc').write_text('bytecode', encoding='utf-8')\n"
        "if os.environ.get('AGY_WRITE') == '1':\n"
        "    pathlib.Path('reviewed.txt').write_text('changed\\n', encoding='utf-8')\n"
        "if sys.stdout.isatty():\n"
        "    observed_root = os.environ.get('AGY_OBSERVED_REPO', os.getcwd())\n"
        "    observed_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=observed_root, check=True, capture_output=True, text=True).stdout.strip()\n"
        "    print('WITNESSD_AGY_CONTEXT ' + json.dumps({'repo_root': observed_root, 'git_head': observed_head}, sort_keys=True))\n"
        "    if os.environ.get('AGY_REVIEW_MODE') == 'intent-only':\n"
        "        print('I will inspect the requested files now.')\n"
        "    else:\n"
        "        print('Review findings:')\n"
        "        print('low README.md:1 review-only smoke finding')\n"
        "    if os.environ.get('AGY_COMPLETION_MODE', 'correct') != 'missing':\n"
        "        print('WITNESSD_AGY_COMPLETE ' + json.dumps({'status': 'complete'}, sort_keys=True))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class OrroCheckBlockerTest(unittest.TestCase):
    def test_no_checks_declared_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            code, payload, err = _run(["orro", "check", "--repo", str(repo), "--json"])
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(payload["kind"], "orro-companion-result")
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_CHECK_NO_CHECKS_DECLARED"
            )
            self.assertIn("required_input_or_grant", payload["error"])
            self.assertIn("next_command", payload["error"])


class OrroCheckVerifyTest(unittest.TestCase):
    def _run_check(
        self, tmp: str, checks: list[str]
    ) -> tuple[tuple[int, object, str], Path]:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        _seed_repo(repo)
        argv = [
            "orro",
            "check",
            "--repo",
            str(repo),
            "--home",
            str(root / "home"),
            "--run-dir",
            str(root / "run"),
            "--no-review",
            "--json",
        ]
        for check in checks:
            argv += ["--check", check]
        return _run(argv), root

    def test_passing_check_yields_pass_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (code, payload, err), root = self._run_check(tmp, ["true"])
            self.assertEqual(code, 0, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["kind"], "orro-companion-manifest")
            self.assertEqual(payload["scope"], "state-verified")
            self.assertIs(payload["reviewed_work_execution_observed"], False)
            self.assertIs(payload["verification_checks_executed_observed"], True)
            self.assertEqual(payload["execution_adapter_lanes_spawned"], 0)
            self.assertIs(payload["boundary"]["depone_verified"], False)
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertNotIn("review_ref", payload)
            self.assertNotIn("declared_intent", payload)
            self.assertNotIn("declared_intent_ref", payload)
            self.assertNotIn("intent_drift_advisory", payload)
            self.assertNotIn("intent_alignment_note", payload)
            manifest = json.loads(
                (root / "run" / "companion-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["verdict_ref"]["decision"], "pass")

    def test_failing_check_yields_blocked_verdict_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (code, payload, err), root = self._run_check(tmp, ["false"])
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["kind"], "orro-companion-manifest")
            self.assertIn(
                payload["verdict_ref"]["decision"],
                {"blocked", "blocked-explicit"},
            )
            self.assertIs(payload["reviewed_work_execution_observed"], False)
            self.assertTrue((root / "run" / "companion-manifest.json").is_file())

    def test_declared_intent_is_sealed_and_cited_without_review_drift_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intent = {
                "intent": "Verify the existing work in its human context.",
                "non_goals": ["paper-chat"],
            }
            intent_path = root / "intent.json"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            code, payload, err = _run(
                [
                    "orro",
                    "check",
                    "--repo",
                    str(repo),
                    "--home",
                    str(root / "home-intent"),
                    "--run-dir",
                    str(root / "run-intent"),
                    "--check",
                    "true",
                    "--intent",
                    str(intent_path),
                    "--no-review",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            sidecar = root / "run-intent" / "declared-intent.json"
            self.assertEqual(payload["declared_intent"], intent)
            self.assertEqual(payload["declared_intent_ref"]["path"], str(sidecar))
            self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), intent)
            self.assertNotIn("intent_drift_advisory", payload)
            self.assertNotIn("intent_alignment_note", payload)

    def test_invalid_declared_intent_returns_structured_companion_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, payload, err = _run(
                [
                    "orro",
                    "check",
                    "--repo",
                    str(root),
                    "--check",
                    "true",
                    "--intent",
                    "inline intent",
                    "--json",
                ]
            )
            self.assertEqual(code, 2, err)
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_INTENT_READ_FAILED")
            self.assertIn("Schema:", payload["error"]["message"])


class ZeroExecutionInvariantTest(unittest.TestCase):
    def test_execution_adapter_count_is_derived_from_sealed_ledger(self) -> None:
        from witnessd.cli.companion import _execution_adapter_lane_count

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "team-ledger.json"
            ledger.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {"lane_id": "check", "runner_adapter_kind": "shell"},
                            {"lane_id": "worker", "runner_adapter_kind": "codex"},
                            {"lane_id": "review", "runner_adapter_kind": "external"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(_execution_adapter_lane_count(ledger), 2)

    def test_unreadable_ledger_falls_back_to_zero(self) -> None:
        from witnessd.cli.companion import _execution_adapter_lane_count

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "team-ledger.json"
            ledger.write_text("not-json", encoding="utf-8")
            self.assertEqual(_execution_adapter_lane_count(ledger), 0)

    def test_non_shell_adapter_is_rejected(self) -> None:
        from witnessd.cli.companion import _assert_no_execution_adapter

        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "rlp.json"
            plan.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "lane_id": "x",
                                "adapter": "codex",
                                "region": ["."],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError) as ctx:
                _assert_no_execution_adapter(plan)
            self.assertIn("ERR_ORRO_CHECK_EXECUTION_LANE_FORBIDDEN", str(ctx.exception))

    def test_shell_only_plan_passes(self) -> None:
        from witnessd.cli.companion import _assert_no_execution_adapter

        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "rlp.json"
            plan.write_text(
                json.dumps(
                    {"lanes": [{"lane_id": "x", "adapter": "shell", "region": []}]}
                ),
                encoding="utf-8",
            )
            _assert_no_execution_adapter(plan)


class OrroCheckReviewTest(unittest.TestCase):
    def test_review_attaches_advisory_ref_without_changing_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            bindir = root / "bin"
            bindir.mkdir()
            fake_agy = _fake_agy(bindir)
            code, payload, err = _run(
                [
                    "orro",
                    "check",
                    "--repo",
                    str(repo),
                    "--home",
                    str(root / "home"),
                    "--run-dir",
                    str(root / "run"),
                    "--check",
                    "true",
                    "--reviewer",
                    "agy",
                    "--reviewer-binary",
                    str(fake_agy),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["scope"], "state-verified-and-reviewed")
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertIn("review_ref", payload)
            self.assertIs(payload["review_ref"]["advisory"], True)
            self.assertTrue((root / "run" / "orro-review-summary.json").is_file())

    def test_review_goal_includes_intent_and_drift_stays_advisory(self) -> None:
        from witnessd.cli import companion

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            bindir = root / "bin"
            bindir.mkdir()
            fake_agy = _fake_agy(bindir)
            intent = {
                "intent": "Review for reading-flow clarity.",
                "non_goals": ["review-only chatbot"],
            }
            intent_path = root / "intent.json"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            original = companion._invoke_phase
            flowplan_goals: dict[str, str] = {}

            def capture_goals(argv: list[str]) -> tuple[int, object, str]:
                if argv[0] == "flowplan":
                    profile = argv[argv.index("--profile") + 1]
                    flowplan_goals[profile] = argv[1]
                return original(argv)

            with patch("witnessd.cli.companion._invoke_phase", side_effect=capture_goals):
                code, payload, err = _run(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--home",
                        str(root / "home"),
                        "--run-dir",
                        str(root / "run"),
                        "--check",
                        "true",
                        "--intent",
                        str(intent_path),
                        "--reviewer",
                        "agy",
                        "--reviewer-binary",
                        fake_agy,
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertNotIn(intent["intent"], flowplan_goals["verification-only"])
            self.assertIn(intent["intent"], flowplan_goals["review-only"])
            self.assertIn(intent["non_goals"][0], flowplan_goals["review-only"])
            self.assertEqual(payload["intent_drift_advisory"][0]["matched_token"], "review-only")
            self.assertIs(
                payload["intent_drift_advisory"][0]["can_change_evidence_verdict"],
                False,
            )
            self.assertIn("lexical-screening absence only", payload["intent_alignment_note"])


class ReviewerUnavailableTest(unittest.TestCase):
    def test_missing_reviewer_binary_skips_review_and_preserves_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            code, payload, err = _run(
                [
                    "orro",
                    "check",
                    "--repo",
                    str(repo),
                    "--home",
                    str(root / "home"),
                    "--run-dir",
                    str(root / "run"),
                    "--check",
                    "true",
                    "--reviewer",
                    "agy",
                    "--reviewer-binary",
                    str(root / "does-not-exist-agy"),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["kind"], "orro-companion-manifest")
            self.assertNotIn("decision", payload)
            self.assertEqual(
                payload["review_skipped"]["code"],
                "ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
            )
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertNotIn("review_ref", payload)
            manifest = json.loads(
                (root / "run" / "companion-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest, payload)

    def test_failed_reviewer_lane_skips_review_and_preserves_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            reviewer = root / "agy"
            reviewer.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            reviewer.chmod(reviewer.stat().st_mode | stat.S_IEXEC)
            code, payload, err = _run(
                [
                    "orro",
                    "check",
                    "--repo",
                    str(repo),
                    "--home",
                    str(root / "home"),
                    "--run-dir",
                    str(root / "run"),
                    "--check",
                    "true",
                    "--reviewer",
                    "agy",
                    "--reviewer-binary",
                    str(reviewer),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertEqual(
                payload["review_skipped"]["code"],
                "ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
            )

    def test_failed_review_plan_skips_review_and_preserves_pass(self) -> None:
        from witnessd.cli import companion

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            bindir = root / "bin"
            bindir.mkdir()
            fake_agy = _fake_agy(bindir)
            invoke_phase = companion._invoke_phase

            def fail_review_flowplan(argv: list[str]) -> tuple[int, object, str]:
                if argv[0] == "flowplan" and "review-only" in argv:
                    return 1, {}, "synthetic review flowplan failure"
                return invoke_phase(argv)

            with patch(
                "witnessd.cli.companion._invoke_phase",
                side_effect=fail_review_flowplan,
            ):
                code, payload, err = _run(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--home",
                        str(root / "home"),
                        "--run-dir",
                        str(root / "run"),
                        "--check",
                        "true",
                        "--reviewer",
                        "agy",
                        "--reviewer-binary",
                        fake_agy,
                        "--json",
                    ]
                )
            self.assertEqual(code, 0, err)
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertEqual(
                payload["review_skipped"]["code"],
                "ERR_ORRO_CHECK_REVIEW_PLAN_BLOCKED",
            )


class OrroCheckHumanOutputTest(unittest.TestCase):
    def test_human_output_labels_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            out, errbuf = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(errbuf):
                code = main(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--home",
                        str(root / "home"),
                        "--run-dir",
                        str(root / "run"),
                        "--check",
                        "true",
                        "--no-review",
                    ]
                )
            text = out.getvalue()
            self.assertEqual(code, 0, errbuf.getvalue())
            self.assertIn("VERIFICATION", text)
            self.assertIn("NOT observed-executed", text)
            self.assertIn("0 execution-adapter lanes", text)

    def test_human_output_opens_with_declared_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            intent_path = root / "intent.json"
            intent_path.write_text(
                json.dumps(
                    {
                        "intent": "Verify the requested boundary.",
                        "non_goals": ["paper-chat"],
                    }
                ),
                encoding="utf-8",
            )
            out, errbuf = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(errbuf):
                code = main(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--home",
                        str(root / "home"),
                        "--run-dir",
                        str(root / "run"),
                        "--check",
                        "true",
                        "--intent",
                        str(intent_path),
                        "--no-review",
                    ]
                )
            lines = out.getvalue().splitlines()
            self.assertEqual(code, 0, errbuf.getvalue())
            self.assertEqual(lines[0], "orro check — evidence & review for work you already drove")
            self.assertIn("Verify the requested boundary.", lines[2])
            self.assertIn("paper-chat", out.getvalue())
            self.assertLess(
                out.getvalue().index("Verify the requested boundary."),
                out.getvalue().index("VERIFICATION"),
            )

    def test_human_output_prominently_reports_skipped_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            out, errbuf = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(errbuf):
                code = main(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--home",
                        str(root / "home"),
                        "--run-dir",
                        str(root / "run"),
                        "--check",
                        "true",
                        "--reviewer",
                        "agy",
                        "--reviewer-binary",
                        str(root / "missing-agy"),
                    ]
                )
            text = out.getvalue()
            self.assertEqual(code, 0, errbuf.getvalue())
            self.assertIn("VERIFICATION", text)
            self.assertIn("⚠ review skipped:", text)
            self.assertIn("install agy, or pass --no-review", text)
            self.assertIn("BOUNDARY", text)


if __name__ == "__main__":
    unittest.main()
