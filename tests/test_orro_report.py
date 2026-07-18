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
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.orro_report import render_text_report
from witnessd.orro_team_surface import apply_task_prompt_to_role_lane_plan


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO report fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: orro-report-fixture\n---\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _write_shell_rolepack(root: Path) -> Path:
    path = root / "shell-rolepack.json"
    path.write_text(
        json.dumps(
            {
                "kind": "moonweave-rolepack",
                "schema_version": "0.2",
                "name": "shell-test",
                "grants": [
                    {
                        "role_id": "runner",
                        "capability": "execute",
                        "adapters": ["shell"],
                        "write_scope": ["orro/proof.txt"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


class OrroReportTests(unittest.TestCase):
    def _init_home(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()
        _seed_repo(repo)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["init", "--home", str(home), "--depone-root", str(_depone_root())]),
                0,
            )
        return repo, home

    def _flowplan_out(self, root: Path, goal: str) -> Path:
        out = root / "workflow-plan.json"
        with redirect_stdout(io.StringIO()):
            code = main(
                [
                    "orro",
                    "flowplan",
                    goal,
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(code, 0)
        return out

    def _role_lane_plan_out(self, root: Path, goal: str) -> Path:
        out = root / "role-lane-plan.json"
        rolepack = _write_shell_rolepack(root)
        with redirect_stdout(io.StringIO()):
            code = main(
                [
                    "orro",
                    "flowplan",
                    goal,
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--rolepack-file",
                    str(rolepack),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out.read_text(encoding="utf-8"))
        patched = apply_task_prompt_to_role_lane_plan(
            payload,
            task=f"Perform the declared {goal} task",
        )["role_lane_plan"]
        out.write_text(json.dumps(patched), encoding="utf-8")
        return out

    def _proofrun(
        self,
        root: Path,
        *,
        with_workflow: bool = False,
    ) -> tuple[Path, Path]:
        repo, home = self._init_home(root)
        args = [
            "orro",
            "proofrun",
            "write report fixture",
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--max-parallel",
            "1",
        ]
        if with_workflow:
            workflow_plan = self._flowplan_out(root, "write report fixture")
            role_lane_plan = self._role_lane_plan_out(root, "write report fixture")
            args.extend(["--workflow-plan", str(workflow_plan), "--role-lane-plan", str(role_lane_plan)])
        args.append("--allow-reference-adapter")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(args)
        self.assertEqual(code, 0, stdout.getvalue())
        return home, Path(json.loads(stdout.getvalue())["run_dir"])

    def _proofcheck(self, home: Path, run_dir: Path) -> dict:
        out = run_dir / "proofcheck-verdict.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def _handoff(self, run_dir: Path) -> dict:
        out = run_dir / "orro-handoff.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "handoff", str(run_dir), "--out", str(out)])
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def _report(self, run_dir: Path, home: Path, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "report", str(run_dir), "--home", str(home), "--json", *extra])
        return code, json.loads(stdout.getvalue())

    def test_report_after_proofrun_recommends_proofcheck_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._proofrun(Path(tmp))
            with patch("witnessd.cli.advisory._run_depone_json", side_effect=AssertionError("report ran proofcheck")):
                code, payload = self._report(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["kind"], "orro-report")
            self.assertEqual(payload["summary"]["state"], "needs-proofcheck")
            self.assertIn("proofcheck", payload["summary"]["recommended_next_action"])
            self.assertFalse(payload["verification"]["proofcheck_verdict_present"])
            self.assertFalse(payload["handoff"]["ready_for_handoff"])
            self.assertFalse(payload["summary"]["complete"])
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())

    def test_report_after_passing_proofcheck_is_ready_for_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._proofrun(Path(tmp))
            self._proofcheck(home, run_dir)
            code, payload = self._report(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["summary"]["state"], "ready-for-handoff")
            self.assertEqual(payload["verification"]["decision"], "pass")
            self.assertEqual(payload["verification"]["verified_by"], "Depone")
            self.assertFalse(payload["handoff"]["handoff_present"])
            self.assertTrue(payload["handoff"]["ready_for_handoff"])
            self.assertIn("handoff", payload["summary"]["recommended_next_action"])

    def test_report_after_handoff_is_complete_without_merge_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._proofrun(Path(tmp))
            self._proofcheck(home, run_dir)
            self._handoff(run_dir)
            code, payload = self._report(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["summary"]["state"], "complete")
            self.assertTrue(payload["summary"]["complete"])
            self.assertTrue(payload["handoff"]["handoff_present"])
            self.assertFalse(payload["handoff"]["approves_merge"])
            self.assertFalse(payload["handoff"]["raises_assurance"])
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])

    def test_report_blocks_stale_handoff_instead_of_claiming_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root = root / "first"
            first_root.mkdir()
            first_home, first_run_dir = self._proofrun(first_root)
            self._proofcheck(first_home, first_run_dir)
            self._handoff(first_run_dir)

            second_root = root / "second"
            second_root.mkdir()
            second_home, second_run_dir = self._proofrun(second_root)
            self._proofcheck(second_home, second_run_dir)
            (second_run_dir / "orro-handoff.json").write_text(
                (first_run_dir / "orro-handoff.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            code, payload = self._report(second_run_dir, second_home)

            self.assertEqual(code, 1)
            self.assertEqual(payload["summary"]["state"], "blocked")
            self.assertFalse(payload["summary"]["complete"])
            self.assertFalse(payload["handoff"]["ready_for_handoff"])
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH")

    def test_report_blocks_non_pass_scout_only_and_malformed_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._proofrun(root)
            (run_dir / "proofcheck-verdict.json").write_text(
                json.dumps({"decision": "fail"}) + "\n",
                encoding="utf-8",
            )
            code, payload = self._report(run_dir, home)
            self.assertEqual(code, 1)
            self.assertEqual(payload["summary"]["state"], "blocked")
            self.assertFalse(payload["handoff"]["ready_for_handoff"])

            scout_dir = root / "scout-only"
            scout_dir.mkdir()
            (scout_dir / "workflow-plan.json").write_text("{}", encoding="utf-8")
            code, payload = self._report(scout_dir, home)
            self.assertEqual(code, 1)
            self.assertIn(payload["summary"]["state"], {"blocked", "evidence-pending"})
            self.assertFalse(payload["summary"]["complete"])

            malformed = root / "malformed"
            malformed.mkdir()
            (malformed / "proofcheck-verdict.json").write_text("not-json", encoding="utf-8")
            code, payload = self._report(malformed, home)
            self.assertEqual(code, 1)
            self.assertEqual(payload["summary"]["state"], "blocked")

    def test_report_rejects_missing_and_unbound_verdicts_without_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._proofrun(root)
            code, payload = self._report(root / "missing-run", home)
            self.assertEqual(code, 2)
            self.assertEqual(payload["summary"]["state"], "invalid-run-dir")

            (run_dir / "proofcheck-verdict.json").write_text(
                json.dumps({"decision": "pass"}) + "\n",
                encoding="utf-8",
            )
            code, payload = self._report(run_dir, home)
            self.assertEqual(code, 1)
            self.assertEqual(payload["summary"]["state"], "blocked")
            self.assertFalse(payload["handoff"]["ready_for_handoff"])

    def test_report_includes_workflow_role_auto_and_workstyle_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._proofrun(root, with_workflow=True)
            (run_dir / "orro-auto-session.json").write_text(
                json.dumps(
                    {
                        "kind": "orro-auto-session",
                        "mode": "until-complete",
                        "steps_executed": 1,
                        "decision_final": "ready-for-handoff",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._proofcheck(home, run_dir)
            workstyle = root / "workstyle.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["orro", "advise", "fix parser bug", "--repo", str(root), "--out", str(workstyle), "--json"]),
                    0,
                )

            code, payload = self._report(run_dir, home, "--workstyle-decision", str(workstyle))

            self.assertEqual(code, 0)
            self.assertTrue(payload["workflow"]["workflow_plan_present"])
            self.assertTrue(payload["workflow"]["role_lane_plan_present"])
            self.assertTrue(payload["workflow"]["role_dispatch_present"])
            self.assertEqual(payload["workstyle"]["task_class"], "code-change")
            self.assertEqual(payload["auto"]["session"]["steps_executed"], 1)
            self.assertTrue(payload["execution"]["runner_roles"])
            self.assertEqual(payload["execution"]["lane_count"], 1)
            self.assertEqual(payload["execution"]["distinct_adapter_count"], 1)
            self.assertEqual(payload["execution"]["distinct_model_count"], 0)
            self.assertFalse(payload["execution"]["multi_model_execution"])

    def test_report_out_writes_same_json_and_text_mode_is_human_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._proofrun(root)
            out = run_dir / "orro-report.json"
            code, payload = self._report(run_dir, home, "--out", str(out))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                text_code = main(["orro", "report", str(run_dir), "--home", str(home)])
            text = stdout.getvalue()
            self.assertEqual(text_code, 0)
            self.assertIn("ORRO Report", text)
            self.assertIn("State: needs-proofcheck", text)
            self.assertIn("Next:", text)
            self.assertIn("Human review:", text)

    def test_single_lane_report_is_explicitly_not_multi_model_or_team_execution(
        self,
    ) -> None:
        payload = {
            "goal": "fix parser",
            "summary": {
                "state": "needs-proofcheck",
                "recommended_next_action": "proofcheck",
            },
            "workflow": {"profile": "code-change"},
            "execution": {
                "proofrun_evidence_present": True,
                "lane_count": 1,
                "distinct_adapter_count": 1,
                "distinct_model_count": 1,
                "multi_model_execution": False,
                "policy_selected": True,
            },
            "verification": {"proofcheck_verdict_present": False},
            "handoff": {"handoff_present": False},
            "human_review": {"focus": []},
            "do_not_trust": ["role-lane plan alone"],
        }

        text = render_text_report(payload)
        execution_line = next(
            line for line in text.splitlines() if line.startswith("Execution:")
        )

        self.assertIn("single-lane policy selection", execution_line)
        self.assertNotIn("team", execution_line.lower())
        self.assertNotIn("multi-model", execution_line.lower())
        self.assertNotIn("parallel", execution_line.lower())

    def test_single_lane_manual_report_does_not_claim_policy_selection(self) -> None:
        payload = {
            "goal": "fix parser",
            "summary": {
                "state": "needs-proofcheck",
                "recommended_next_action": "proofcheck",
            },
            "workflow": {"profile": "code-change"},
            "execution": {
                "proofrun_evidence_present": True,
                "lane_count": 1,
                "distinct_adapter_count": 1,
                "distinct_model_count": 1,
                "multi_model_execution": False,
                "policy_selected": False,
            },
            "verification": {"proofcheck_verdict_present": False},
            "handoff": {"handoff_present": False},
            "human_review": {"focus": []},
            "do_not_trust": ["role-lane plan alone"],
        }

        execution_line = next(
            line
            for line in render_text_report(payload).splitlines()
            if line.startswith("Execution:")
        )

        self.assertIn("single-lane execution", execution_line)
        self.assertNotIn("policy", execution_line.lower())

    def test_report_module_aliases_and_no_artifact_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._proofrun(root)
            before = sorted(p.relative_to(run_dir) for p in run_dir.rglob("*") if p.is_file())
            repo_root = Path(__file__).resolve().parents[1]
            env = os.environ.copy()
            depone_root = str(_depone_root())
            env["PYTHONPATH"] = depone_root if not env.get("PYTHONPATH") else f"{depone_root}{os.pathsep}{env['PYTHONPATH']}"
            module = subprocess.run(
                [sys.executable, "-m", "orro", "report", str(run_dir), "--home", str(home), "--json"],
                cwd=repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            alias = subprocess.run(
                [sys.executable, "-m", "witnessd", "orro", "report", str(run_dir), "--home", str(home), "--json"],
                cwd=repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            after = sorted(p.relative_to(run_dir) for p in run_dir.rglob("*") if p.is_file())

            self.assertEqual(module.returncode, 0, module.stderr)
            self.assertEqual(alias.returncode, 0, alias.stderr)
            self.assertEqual(json.loads(module.stdout)["summary"], json.loads(alias.stdout)["summary"])
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
