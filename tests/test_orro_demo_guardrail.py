from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from orro.__main__ import main as orro_main
from witnessd.__main__ import main
from witnessd.orro_workflow import (
    OrroWorkflowError,
    compile_role_lane_plan,
    compile_workflow_plan,
)
from witnessd.orro_team_surface import build_rolepack_scaffold


DEPONE_ROOT = Path(__file__).resolve().parents[2] / "depone"


def _seed_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "guardrail@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Guardrail Demo"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("guardrail demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _run(argv: list[str]) -> tuple[int, dict[str, object], str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    raw = stdout.getvalue().strip()
    payload = json.loads(raw) if raw else {}
    return code, payload, stderr.getvalue()


class ShellCommandLaneCompilationTests(unittest.TestCase):
    def test_code_change_shell_commands_keep_granted_write_scope_region(self) -> None:
        workflow = compile_workflow_plan(
            goal="write generated source", profile="code-change"
        )
        rolepack = build_rolepack_scaffold(
            template=None,
            roles=["runner:shell"],
            write_scope=["src/**"],
        )

        plan = compile_role_lane_plan(
            workflow_plan=workflow,
            lane_adapter="shell",
            rolepack=rolepack,
            command_commands=[
                "mkdir -p src && echo generated > src/generated.txt",
                "touch src/second.txt",
            ],
        )

        self.assertEqual(len(plan["lanes"]), 1)
        lane = plan["lanes"][0]
        self.assertEqual(lane["adapter"], "shell")
        self.assertEqual(lane["region"], ["src/**"])
        self.assertEqual(lane["granted_write_scope"], ["src/**"])
        self.assertEqual(lane["lane_intent"], "implementation")
        self.assertEqual(
            lane["commands"],
            [
                "mkdir -p src && echo generated > src/generated.txt",
                "touch src/second.txt",
            ],
        )
        self.assertNotIn("check_commands", lane)

    def test_commands_reject_ai_adapter_with_actionable_error(self) -> None:
        workflow = compile_workflow_plan(goal="write source", profile="code-change")
        rolepack = build_rolepack_scaffold(
            template=None,
            roles=["runner:codex"],
            write_scope=["src/**"],
        )

        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(
                workflow_plan=workflow,
                lane_adapter="codex",
                rolepack=rolepack,
                command_commands=["touch src/generated.txt"],
            )

        self.assertEqual(cm.exception.code, "ERR_ORRO_COMMAND_ADAPTER_UNSUPPORTED")
        self.assertIn("--lane-adapter shell", str(cm.exception))
        self.assertIn("prompt-driven", str(cm.exception))

    def test_commands_and_checks_are_mutually_exclusive(self) -> None:
        workflow = compile_workflow_plan(goal="write source", profile="code-change")

        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(
                workflow_plan=workflow,
                lane_adapter="shell",
                check_commands=["true"],
                command_commands=["touch src/generated.txt"],
            )

        self.assertEqual(cm.exception.code, "ERR_ORRO_COMMAND_CHECK_CONFLICT")
        self.assertIn("mutually exclusive", str(cm.exception))

    def test_commands_do_not_override_explicit_verification_only_intent(self) -> None:
        workflow = compile_workflow_plan(
            goal="write source",
            profile="code-change",
            lane_intent="verification-only",
        )

        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(
                workflow_plan=workflow,
                lane_adapter="shell",
                command_commands=["touch src/generated.txt"],
            )

        self.assertEqual(cm.exception.code, "ERR_ORRO_COMMAND_PROFILE_UNSUPPORTED")
        self.assertIn("implementation lane intent", str(cm.exception))

    def test_cli_errors_are_structured_and_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = [
                "flowplan",
                "write source",
                "--root",
                str(root),
                "--profile",
                "code-change",
                "--role-lanes-out",
                str(root / "role-lane-plan.json"),
                "--write-scope",
                "src/**",
                "--command",
                "touch src/generated.txt",
                "--json",
            ]

            code, payload, _error = _run(
                [*common, "--lane-adapter", "codex"]
            )
            self.assertEqual(code, 1)
            error = payload["error"]
            self.assertEqual(
                error["code"], "ERR_ORRO_COMMAND_ADAPTER_UNSUPPORTED"
            )
            for field in ("reason", "required_input_or_grant", "next_command"):
                self.assertTrue(error[field])
            self.assertIn("--lane-adapter shell", error["next_command"])

            code, payload, _error = _run([*common, "--check", "true"])
            self.assertEqual(code, 1)
            error = payload["error"]
            self.assertEqual(error["code"], "ERR_ORRO_COMMAND_CHECK_CONFLICT")
            self.assertIn("choose exactly one", error["required_input_or_grant"])


class ShellGuardrailDeponeEndToEndTests(unittest.TestCase):
    maxDiff = None

    def _proofcheck(self, root: Path, *, violate: bool) -> dict[str, object]:
        repo = root / "repo"
        _seed_repo(repo)
        home = root / "home"
        run_dir = root / "run"
        runner = root / "runner"
        run_dir.mkdir()
        runner.mkdir()
        workflow_plan = run_dir / "workflow-plan.json"
        role_lane_plan = run_dir / "role-lane-plan.json"
        verdict_path = run_dir / "proofcheck-verdict.json"
        command = (
            "echo guardrail-demo > outside.txt"
            if violate
            else "mkdir -p src && echo guardrail-demo > src/generated.txt"
        )

        code, _payload, error = _run(
            [
                "init",
                "--home",
                str(home),
                "--repo",
                str(repo),
                "--depone-root",
                str(DEPONE_ROOT),
            ]
        )
        self.assertEqual(code, 0, error)

        code, _payload, error = _run(
            [
                "flowplan",
                "write a guarded file",
                "--root",
                str(repo),
                "--profile",
                "code-change",
                "--out",
                str(workflow_plan),
                "--role-lanes-out",
                str(role_lane_plan),
                "--lane-adapter",
                "shell",
                "--write-scope",
                "src/**",
                "--command",
                command,
                "--json",
            ]
        )
        self.assertEqual(code, 0, error)

        code, _payload, error = _run(
            [
                "proofrun",
                "write a guarded file",
                "--repo",
                str(repo),
                "--home",
                str(home),
                "--workflow-plan",
                str(workflow_plan),
                "--role-lane-plan",
                str(role_lane_plan),
                "--adapter",
                "shell",
                "--runner-sandbox",
                str(runner),
                "--run-dir",
                str(run_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0, error)

        code, payload, _error = _run(
            [
                "proofcheck",
                "--evidence-dir",
                str(run_dir),
                "--home",
                str(home),
                "--out",
                str(verdict_path),
                "--json",
            ]
        )
        if violate:
            self.assertEqual(code, 1, payload)
        else:
            self.assertEqual(code, 0, payload)
        self.assertTrue(verdict_path.is_file())
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("policy_conformance"), verdict["policy_conformance"])
        return verdict

    def test_in_scope_command_reaches_real_depone_policy_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._proofcheck(Path(tmp), violate=False)

        policy = verdict["policy_conformance"]
        self.assertEqual(policy["overall"], "pass")
        write_scope = next(axis for axis in policy["axes"] if axis["axis"] == "write_scope")
        self.assertEqual(write_scope["status"], "pass")
        self.assertFalse(write_scope["blocks_handoff"])

    def test_out_of_scope_command_reaches_real_depone_policy_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._proofcheck(Path(tmp), violate=True)

        policy = verdict["policy_conformance"]
        self.assertEqual(policy["overall"], "fail")
        write_scope = next(axis for axis in policy["axes"] if axis["axis"] == "write_scope")
        self.assertEqual(write_scope["status"], "fail")
        self.assertEqual(
            write_scope["error_code"],
            "ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION",
        )
        self.assertEqual(write_scope["evidence_path"], "outside.txt")
        self.assertTrue(write_scope["blocks_handoff"])


class OrroDemoTests(unittest.TestCase):
    def _demo(self, *extra: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = orro_main(
                ["demo", "--depone-root", str(DEPONE_ROOT), *extra]
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_demo_prints_depone_policy_pass(self) -> None:
        code, stdout, stderr = self._demo()

        self.assertEqual(code, 0, stderr)
        self.assertIn(
            "Policy conformance: PASS — touched files ⊆ declared write-scope (src/**)",
            stdout,
        )
        self.assertIn("deterministic shell execution standing in for an agent", stdout)

    def test_demo_violation_prints_blocking_depone_policy_fail(self) -> None:
        code, stdout, stderr = self._demo("--violate")

        self.assertEqual(code, 1, stderr)
        self.assertIn(
            "Policy conformance: FAIL — write_scope violated: outside.txt outside src/**  "
            "(blocks_handoff: true)",
            stdout,
        )


class OrroFlowCommandThreadingTests(unittest.TestCase):
    def test_flow_threads_declared_commands_into_shell_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _seed_repo(repo)
            run_dir = root / "run"
            code, payload, error = _run(
                [
                    "orro",
                    "flow",
                    "write generated source file",
                    "--repo",
                    str(repo),
                    "--home",
                    str(root / "home"),
                    "--run-dir",
                    str(run_dir),
                    "--runner-sandbox",
                    str(root / "runner"),
                    "--write-scope",
                    "src/**",
                    "--adapter",
                    "shell",
                    "--command",
                    "mkdir -p src && echo generated > src/generated.txt",
                    "--json",
                ]
            )

            self.assertEqual(code, 0, error or payload)
            self.assertEqual(payload["decision"], "pass")
            lane_plan = json.loads(
                (run_dir / "role-lane-plan.json").read_text(encoding="utf-8")
            )
            lane = lane_plan["lanes"][0]
            self.assertEqual(lane["region"], ["src/**"])
            self.assertEqual(
                lane["commands"],
                ["mkdir -p src && echo generated > src/generated.txt"],
            )


if __name__ == "__main__":
    unittest.main()
