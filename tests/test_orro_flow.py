from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "orro-flow@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Flow"], cwd=repo, check=True
    )
    (repo / "README.md").write_text("# ORRO flow fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


class OrroFlowTests(unittest.TestCase):
    def test_help_exposes_guided_flow_options(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            main(["orro", "flow", "--help"])

        self.assertEqual(ctx.exception.code, 0)
        help_text = stdout.getvalue()
        for option in (
            "--repo",
            "--write-scope",
            "--adapter",
            "--runner-sandbox",
            "--rolepack-file",
            "--role-lane-tier",
            "--run-dir",
            "--allow-reference-adapter",
            "--verification-only",
            "--json",
        ):
            self.assertIn(option, help_text)

    def test_repo_flag_targets_the_requested_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target-repo"
            repo.mkdir()
            _seed_repo(repo)
            run_dir = root / "run"
            runner_sandbox = root / "runner"
            stdout = io.StringIO()

            with (
                patch(
                    "witnessd.cli.flow._invoke_orro_flow_phase",
                    return_value=(
                        2,
                        {
                            "error": {
                                "code": "ERR_TEST_INIT_BLOCKED",
                                "message": "stop after observing init argv",
                            }
                        },
                        "",
                    ),
                ) as invoke,
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "make a change",
                        "--repo",
                        str(repo),
                        "--write-scope",
                        "pkg/**",
                        "--adapter",
                        "shell",
                        "--run-dir",
                        str(run_dir),
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            init_argv = invoke.call_args.args[0]
            self.assertEqual(init_argv[init_argv.index("--repo") + 1], str(repo.resolve()))
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_TEST_INIT_BLOCKED",
            )

    def test_missing_write_scope_is_a_structured_flowplan_blocker(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "orro",
                    "flow",
                    "create pkg/output.txt",
                    "--adapter",
                    "codex",
                    "--verification-only",
                    "--json",
                ]
            )

        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["kind"], "orro-flow-result")
        self.assertEqual(payload["decision"], "blocked")
        self.assertEqual(payload["blocked_phase"], "flowplan")
        self.assertEqual(payload["phases"], [])
        self.assertEqual(
            payload["error"]["code"], "ERR_ORRO_FLOW_WRITE_SCOPE_REQUIRED"
        )
        for key in ("message", "reason", "required_input_or_grant", "next_command"):
            self.assertTrue(payload["error"][key])
        self.assertIn("--write-scope", payload["error"]["next_command"])
        # advisory next_command must be a runnable form and preserve the
        # user's verification-only declaration (not the invalid -m witnessd flow).
        self.assertIn("python3 -m orro flow", payload["error"]["next_command"])
        self.assertNotIn("python3 -m witnessd flow", payload["error"]["next_command"])
        self.assertIn("--verification-only", payload["error"]["next_command"])
        self.assertNotIn("Traceback", stdout.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_shell_reference_flow_returns_a_structured_first_phase_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "create a package file",
                        "--write-scope",
                        "pkg/**",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-flow-result")
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["blocked_phase"], "flowplan")
            self.assertEqual(
                [phase["phase"] for phase in payload["phases"]],
                ["init", "scout"],
            )
            self.assertTrue(all(phase["status"] == "ok" for phase in payload["phases"]))
            self.assertEqual(payload["run_dir"], str(run_dir.resolve(strict=False)))
            self.assertIn("not granted", payload["error"]["reason"])
            self.assertIn("'shell'", payload["error"]["required_input_or_grant"])
            self.assertIn("flowplan", payload["error"]["next_command"])
            rolepack = json.loads(
                (run_dir / "generated-rolepack.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rolepack["grants"][0]["write_scope"], ["pkg/**"])
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_rolepack_file_cannot_widen_the_command_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            rolepack_path = root / "rolepack.json"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            rolepack_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "wider-than-flow",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["shell"],
                                "model": "reference-shell",
                                "write_scope": ["**"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(io.StringIO()),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "create pkg/output.txt",
                        "--write-scope",
                        "pkg/output.txt",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--rolepack-file",
                        str(rolepack_path),
                        "--verification-only",
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["blocked_phase"], "flowplan")
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_FLOW_WRITE_SCOPE_MISMATCH"
            )
            self.assertIn("exactly match", payload["error"]["reason"])
            self.assertFalse((run_dir / "workflow-plan.json").exists())

    def test_verification_only_flow_declares_runner_intent_with_write_scope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            rolepack_path = root / "rolepack.json"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            rolepack_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "shell-reference",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["shell"],
                                "model": "reference-shell",
                                "write_scope": ["pkg/output.txt"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "create pkg/output.txt",
                        "--write-scope",
                        "pkg/output.txt",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--rolepack-file",
                        str(rolepack_path),
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--allow-reference-adapter",
                        "--verification-only",
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["blocked_phase"], "proofrun")
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_FLOW_PROOFRUN_BLOCKED"
            )
            role_lane_plan = json.loads(
                (run_dir / "role-lane-plan.json").read_text(encoding="utf-8")
            )
            runner_lane = next(
                lane for lane in role_lane_plan["lanes"] if lane["role_id"] == "runner"
            )
            self.assertEqual(runner_lane["lane_intent"], "verification-only")
            self.assertEqual(runner_lane["region"], ["pkg/output.txt"])
            team_ledger = json.loads(
                (run_dir / "team-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                team_ledger["lanes"][0]["lane_intent"], "verification-only"
            )
            self.assertEqual(
                team_ledger["lanes"][0]["touched_files"], ["pkg/output.txt"]
            )
            team_ledger_verdict = json.loads(
                (run_dir / "team-ledger-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(team_ledger_verdict["decision"], "blocked")
            self.assertEqual(
                team_ledger_verdict["errors"][0]["code"],
                "ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED",
            )
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_reference_shell_flow_can_complete_with_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            rolepack_path = root / "rolepack.json"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            rolepack_path.write_text(
                json.dumps(
                    {
                        "kind": "moonweave-rolepack",
                        "schema_version": "0.2",
                        "name": "shell-reference",
                        "grants": [
                            {
                                "role_id": "runner",
                                "capability": "execute",
                                "adapters": ["shell"],
                                "model": "reference-shell",
                                "write_scope": ["pkg/output.txt"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "create pkg/output.txt",
                        "--write-scope",
                        "pkg/output.txt",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--rolepack-file",
                        str(rolepack_path),
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--allow-reference-adapter",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-flow-result")
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["run_dir"], str(run_dir.resolve(strict=False)))
            self.assertEqual(payload["verdict"], str(run_dir / "proofcheck-verdict.json"))
            self.assertEqual(
                [phase["phase"] for phase in payload["phases"]],
                ["init", "scout", "flowplan", "proofrun", "proofcheck"],
            )
            self.assertTrue(all(phase["status"] == "ok" for phase in payload["phases"]))
            self.assertEqual(payload["runner_sandbox"], str(runner_sandbox.resolve()))
            self.assertFalse((run_dir / "team-ledger.json").is_relative_to(runner_sandbox))
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_risky_change_gate_stops_before_proofrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "rotate secret auth token",
                        "--write-scope",
                        "pkg/**",
                        "--adapter",
                        "codex",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-flow-result")
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["blocked_phase"], "flowplan")
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_FLOW_RISKY_CHANGE_REVIEW_REQUIRED",
            )
            self.assertIn("human review", payload["error"]["reason"])
            self.assertIn("flowplan", payload["error"]["next_command"])
            self.assertEqual(
                [phase["phase"] for phase in payload["phases"]],
                ["init", "scout"],
            )
            self.assertFalse((run_dir / "team-ledger.json").exists())
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_risky_change_advisory_preserves_verification_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "observer" / "run"
            runner_sandbox = root / "runner"
            repo.mkdir()
            runner_sandbox.mkdir()
            _seed_repo(repo)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("witnessd.cli.flow.Path.cwd", return_value=repo),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "orro",
                        "flow",
                        "rotate secret auth token",
                        "--write-scope",
                        "pkg/**",
                        "--adapter",
                        "codex",
                        "--runner-sandbox",
                        str(runner_sandbox),
                        "--home",
                        str(home),
                        "--run-dir",
                        str(run_dir),
                        "--verification-only",
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_FLOW_RISKY_CHANGE_REVIEW_REQUIRED",
            )
            next_command = payload["error"]["next_command"]
            # the risky-change advisory rebuilds a flowplan command; under
            # --verification-only it must carry the declaration so a copy-paste
            # reproduces the same intent, not a silently-implementation lane.
            self.assertIn("flowplan", next_command)
            self.assertIn("--lane-intent verification-only", next_command)
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_runner_sandbox_must_be_separate_from_observer_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "flow",
                        "create pkg/output.txt",
                        "--write-scope",
                        "pkg/output.txt",
                        "--adapter",
                        "codex",
                        "--runner-sandbox",
                        str(run_dir / "runner"),
                        "--run-dir",
                        str(run_dir),
                        "--json",
                    ]
                )

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["blocked_phase"], "proofrun")
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_FLOW_RUNNER_NOT_SEPARATED"
            )
            self.assertIn("directory arguments", payload["error"]["reason"])
            self.assertIn(
                "not about where the shell session was started",
                payload["error"]["reason"],
            )
            self.assertIn(
                "--runner-sandbox DIR outside the --run-dir tree",
                payload["error"]["required_input_or_grant"],
            )
            self.assertIn("--runner-sandbox DIR", payload["error"]["next_command"])
            self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
