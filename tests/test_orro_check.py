from __future__ import annotations

import io
import json
import os
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


def _fake_ruff_fixer(directory: Path) -> str:
    path = directory / "ruff"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        "  printf '%s\\n' 'ruff 0.6.9'\n"
        'elif [ "$1" = "check" ] && [ "$2" = "--fix" ]; then\n'
        "  mkdir -p src && printf '%s\\n' 'fixed' > src/health-fixed.txt\n"
        'elif [ "$1" = "check" ]; then\n'
        '  test "$(cat src/health-fixed.txt)" = "fixed"\n'
        "else\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_health_tool(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_ruff_checker(directory: Path) -> str:
    path = directory / "ruff"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf '%s\\n' 'ruff 0.6.9'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_mypy_checker(directory: Path) -> str:
    path = directory / "mypy"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf '%s\\n' 'mypy 1.11.2'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_ruff_scope_violator(directory: Path) -> str:
    path = directory / "ruff"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        "  printf '%s\\n' 'ruff 0.6.9'\n"
        'elif [ "$1" = "check" ] && [ "$2" = "--fix" ]; then\n'
        "  printf '%s\\n' 'outside' > outside.txt\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class OrroCheckBlockerTest(unittest.TestCase):
    def test_apply_without_fix_blocks_before_any_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            with patch(
                "witnessd.cli.companion._invoke_phase",
                side_effect=AssertionError("apply blocker must precede all phases"),
            ):
                code, payload, err = _run(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--apply",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2, err)
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_HEALTH_APPLY_REQUIRES_FIX"
            )
            self.assertIn("--fix", payload["error"]["required_input_or_grant"])
            self.assertIn("--write-scope", payload["error"]["required_input_or_grant"])
            self.assertIn("--apply", payload["error"]["next_command"])

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

    def test_health_without_detected_or_explicit_gates_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            code, payload, err = _run(
                ["orro", "check", "--repo", str(repo), "--health", "--json"]
            )
            self.assertEqual(code, 2, err)
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_HEALTH_NO_GATES_DETECTED"
            )
            self.assertIn("[tool.ruff]", payload["error"]["required_input_or_grant"])
            self.assertIn("--health-plan", payload["error"]["next_command"])

    def test_fix_without_write_scope_blocks_before_any_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            with patch(
                "witnessd.cli.companion._invoke_phase",
                side_effect=AssertionError("scope blocker must precede all phases"),
            ):
                code, payload, err = _run(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--health",
                        "--fix",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2, err)
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_HEALTH_FIX_SCOPE_REQUIRED"
            )
            self.assertIn("--write-scope", payload["error"]["required_input_or_grant"])


class OrroCheckHealthPlanTest(unittest.TestCase):
    def test_health_plan_prints_detected_gates_without_running_a_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text(
                "[tool.ruff]\nline-length = 88\n",
                encoding="utf-8",
            )

            with (
                patch.dict("os.environ", {"PATH": str(repo / "empty-bin")}),
                patch(
                    "witnessd.cli.companion._invoke_phase",
                    side_effect=AssertionError("health-plan must not run a phase"),
                ),
            ):
                code, payload, err = _run(
                    [
                        "orro",
                        "check",
                        "--repo",
                        str(repo),
                        "--health-plan",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertEqual(payload["kind"], "orro-health-plan")
            self.assertEqual(
                payload["gates"],
                [
                    {
                        "gate": "lint",
                        "tool": "ruff",
                        "command": "ruff check .",
                        "version": "unresolved",
                        "enforcement": "block",
                    }
                ],
            )


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

    def test_block_health_failure_sets_fail_and_blocks_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text(
                '[project]\ndependencies = ["black"]\n'
                "[tool.importlinter]\nroot_package = 'pkg'\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure health"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_health_tool(
                bin_dir,
                "black",
                'if [ "$1" = "--version" ]; then echo "black, 24.10.0"; exit 0; fi\nexit 0\n',
            )
            _fake_health_tool(
                bin_dir,
                "lint-imports",
                'if [ "$1" = "--version" ]; then echo "import-linter 2.1"; exit 0; fi\n'
                'echo "architecture contract violated" >&2\nexit 1\n',
            )
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 2, err)
            self.assertEqual(payload["verdict_ref"]["decision"], "fail")
            health = payload["code_health"]
            architecture = next(
                axis for axis in health["gates"] if axis["gate"] == "architecture"
            )
            self.assertEqual(architecture["status"], "fail")
            self.assertEqual(architecture["enforcement"], "block")
            self.assertIs(architecture["blocks_handoff"], True)
            self.assertEqual(
                (run_dir / "health" / "01-architecture.exit").read_text(
                    encoding="utf-8"
                ),
                "1\n",
            )

    def test_advisory_health_failure_is_reported_without_gating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text(
                "[tool.ruff]\n[tool.ruff.lint.mccabe]\nmax-complexity = 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure complexity"],
                cwd=repo,
                check=True,
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_health_tool(
                bin_dir,
                "ruff",
                'if [ "$1" = "--version" ]; then echo "ruff 0.6.9"; exit 0; fi\n'
                'case "$*" in *C901*) echo "C901 too complex"; exit 1;; esac\n'
                "exit 0\n",
            )
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            health = payload["code_health"]
            self.assertEqual(health["overall"], "fail")
            complexity = next(
                axis for axis in health["gates"] if axis["gate"] == "complexity"
            )
            self.assertEqual(complexity["status"], "fail")
            self.assertEqual(complexity["enforcement"], "advisory")
            self.assertIs(complexity["blocks_handoff"], False)

    def test_health_fix_runs_in_scope_before_verify_and_records_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            (repo / "src").mkdir()
            (repo / "src" / "health-fixed.txt").write_text(
                "unfixed\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", "pyproject.toml", "src"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "commit", "-qm", "configure ruff"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_ruff_fixer(bin_dir)
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--fix",
                        "--write-scope",
                        "src/**",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertEqual(payload["code_health"]["verdict"], "pass")
            self.assertEqual(payload["code_health"]["gates"][0]["status"], "pass")
            self.assertEqual(
                payload["code_health"]["fixes_applied"]["ran"],
                ["ruff check --fix ."],
            )
            diff_path = run_dir / "health-fix.diff"
            self.assertEqual(
                payload["code_health"]["fixes_applied"]["diff_ref"]["path"],
                str(diff_path),
            )
            self.assertTrue(diff_path.is_file())
            self.assertIn("src/health-fixed.txt", diff_path.read_text(encoding="utf-8"))
            self.assertIs(
                payload["code_health"]["fixes_applied"]["applied_to_worktree"],
                False,
            )
            self.assertEqual(
                (repo / "src" / "health-fixed.txt").read_text(encoding="utf-8"),
                "unfixed\n",
            )
            self.assertFalse(payload["code_health"]["structural_consistency_covered"])
            self.assertIn(
                "declared deterministic gates ran under observation; the verdict reflects their exit status",
                payload["code_health"]["means"],
            )
            self.assertNotIn("independently re-derived", json.dumps(payload))

    def test_health_fix_apply_mutates_caller_only_after_scope_verified_pass(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            (repo / "src").mkdir()
            fixed_file = repo / "src" / "health-fixed.txt"
            fixed_file.write_text("unfixed\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "pyproject.toml", "src"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "commit", "-qm", "configure ruff"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            ruff = _fake_ruff_fixer(bin_dir)
            run_dir = root / "run"
            self.assertNotEqual(
                subprocess.run([ruff, "check", "."], cwd=repo, check=False).returncode,
                0,
            )

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--fix",
                        "--write-scope",
                        "src/**",
                        "--apply",
                        "--no-review",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertEqual(
                subprocess.run([ruff, "check", "."], cwd=repo, check=False).returncode,
                0,
            )
            self.assertEqual(fixed_file.read_text(encoding="utf-8"), "fixed\n")
            self.assertIn("applied to working tree", payload["_raw"])
            manifest = json.loads(
                (run_dir / "companion-manifest.json").read_text(encoding="utf-8")
            )
            self.assertIs(
                manifest["code_health"]["fixes_applied"]["applied_to_worktree"],
                True,
            )

    def test_health_fix_apply_is_noop_when_no_safe_fixer_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure mypy"], cwd=repo, check=True
            )
            (repo / "README.md").write_text("caller change\n", encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_mypy_checker(bin_dir)
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--fix",
                        "--write-scope",
                        "src/**",
                        "--apply",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err)
            self.assertEqual(
                (repo / "README.md").read_text(encoding="utf-8"), "caller change\n"
            )
            self.assertEqual((run_dir / "health-fix.diff").read_bytes(), b"")
            self.assertIs(
                payload["code_health"]["fixes_applied"]["applied_to_worktree"],
                False,
            )

    def test_health_human_output_carries_the_means_boundary_without_overclaim(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure ruff"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_ruff_checker(bin_dir)

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        "--health",
                        "--no-review",
                    ]
                )

            self.assertEqual(code, 0, err)
            output = payload["_raw"]
            self.assertIn("CODE HEALTH", output)
            self.assertIn("lint", output)
            self.assertIn("ruff", output)
            self.assertIn("0.6.9", output)
            self.assertIn(
                "declared deterministic gates ran under observation; the verdict reflects their exit status",
                output,
            )
            self.assertIn("NOT a claim of good design", output)
            self.assertNotIn("independently re-derived", output)

    def test_configured_but_unavailable_tool_yields_a_named_blocked_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "mypy.ini").write_text("[mypy]\nstrict = true\n", encoding="utf-8")
            subprocess.run(["git", "add", "mypy.ini"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure mypy"], cwd=repo, check=True
            )

            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
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
                        "--health",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 2, err)
            self.assertEqual(payload["code_health"]["verdict"], "fail")
            gate = payload["code_health"]["gates"][0]
            self.assertEqual(gate["tool"], "mypy")
            self.assertEqual(gate["version"], "unresolved")
            self.assertEqual(gate["status"], "fail")
            self.assertIs(gate["blocks_handoff"], True)
            self.assertEqual(
                payload["code_health"]["means"],
                "declared deterministic gates ran under observation; the verdict "
                "reflects their exit status, and is NOT a claim of good design, "
                "correct behavior, or structural consistency",
            )
            self.assertNotIn("passed", json.dumps(payload))

    def test_health_composes_detected_gates_after_explicit_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure ruff"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_ruff_checker(bin_dir)
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--check",
                        "false",
                        "--health",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 2, err)
            plan = json.loads(
                (run_dir / "verify-role-lane-plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                plan["lanes"][0]["check_commands"],
                ["false"],
            )
            health_plan = json.loads(
                (run_dir / "health-run" / "role-lane-plan.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                health_plan["lanes"][0]["check_commands"],
                ["ruff check ."],
            )
            self.assertEqual(
                payload["code_health"]["verdict"], payload["verdict_ref"]["decision"]
            )

    def test_health_fix_outside_declared_scope_is_falsified_by_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "configure ruff"], cwd=repo, check=True
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _fake_ruff_scope_violator(bin_dir)
            run_dir = root / "run"

            with patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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
                        str(run_dir),
                        "--health",
                        "--fix",
                        "--write-scope",
                        "src/**",
                        "--apply",
                        "--no-review",
                        "--json",
                    ]
                )

            self.assertEqual(code, 2, err)
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_HEALTH_FIX_PROOFCHECK_BLOCKED",
            )
            self.assertFalse((repo / "outside.txt").exists())
            fix_verdict = json.loads(
                (run_dir / "health-fix-run" / "proofcheck-verdict.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(fix_verdict["policy_conformance"]["overall"], "fail")
            write_scope = next(
                axis
                for axis in fix_verdict["policy_conformance"]["axes"]
                if axis["axis"] == "write_scope"
            )
            self.assertEqual(write_scope["status"], "fail")

    def test_declared_intent_is_sealed_and_cited_without_review_drift_keys(
        self,
    ) -> None:
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

            with patch(
                "witnessd.cli.companion._invoke_phase", side_effect=capture_goals
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
            self.assertEqual(
                payload["intent_drift_advisory"][0]["matched_token"], "review-only"
            )
            self.assertIs(
                payload["intent_drift_advisory"][0]["can_change_evidence_verdict"],
                False,
            )
            self.assertIn(
                "lexical-screening absence only", payload["intent_alignment_note"]
            )


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
                (root / "run" / "companion-manifest.json").read_text(encoding="utf-8")
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
            self.assertEqual(
                lines[0], "orro check — evidence & review for work you already drove"
            )
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
