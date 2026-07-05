from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: orro-fixture\n---\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


class OrroPublicFlowTests(unittest.TestCase):
    def _module_run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        depone_root = str(_depone_root())
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            depone_root if not current_pythonpath else f"{depone_root}{os.pathsep}{current_pythonpath}"
        )
        return subprocess.run(
            [sys.executable, "-m", "witnessd", *args],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _orro_module_run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        depone_root = str(_depone_root())
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            depone_root if not current_pythonpath else f"{depone_root}{os.pathsep}{current_pythonpath}"
        )
        return subprocess.run(
            [sys.executable, "-m", "orro", *args],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

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

    def _flowplan_out(self, root: Path, goal: str, *, profile: str = "code-change") -> Path:
        out = root / "workflow-plan.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "orro",
                    "flowplan",
                    goal,
                    "--root",
                    str(root),
                    "--profile",
                    profile,
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(out.is_file())
        return out

    def _proofrun(
        self,
        root: Path,
        *,
        orro_alias: bool = False,
        workflow_plan: Path | None = None,
    ) -> tuple[Path, Path, dict]:
        repo, home = self._init_home(root)
        stdout = io.StringIO()
        stderr = io.StringIO()
        command = ["orro", "proofrun"] if orro_alias else ["proofrun"]
        args = [*command, "write two proof files", "--repo", str(repo), "--home", str(home)]
        if workflow_plan is not None:
            args.extend(["--workflow-plan", str(workflow_plan)])
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args)
        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        return home, Path(payload["run_dir"]), payload

    def test_proofrun_alias_reuses_run_surface_without_final_trust_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, payload = self._proofrun(Path(tmp))

            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger.json").is_file())
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())
            self.assertNotIn("final_trust", payload)
            self.assertNotIn("raises_assurance", payload)

    def test_orro_proofrun_normalizes_to_proofrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, payload = self._proofrun(Path(tmp), orro_alias=True)

            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger.json").is_file())

    def test_proofrun_workflow_plan_binding_is_recorded_without_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")

            _home, run_dir, payload = self._proofrun(root, workflow_plan=plan_path)

            self.assertIn("workflow_plan", payload)
            binding_ref = payload["workflow_plan"]
            self.assertEqual(binding_ref["path"], str(run_dir / "workflow-plan.json"))
            self.assertEqual(binding_ref["binding_path"], str(run_dir / "workflow-plan-binding.json"))
            self.assertRegex(binding_ref["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((run_dir / "workflow-plan.json").is_file())
            self.assertTrue((run_dir / "workflow-plan-binding.json").is_file())
            binding = json.loads((run_dir / "workflow-plan-binding.json").read_text(encoding="utf-8"))
            self.assertEqual(binding["kind"], "orro-workflow-plan-binding")
            self.assertEqual(binding["workflow_plan_sha256"], binding_ref["sha256"])
            self.assertEqual(binding["profile"], "code-change")
            self.assertFalse(binding["boundary"]["raises_assurance"])
            self.assertFalse(binding["boundary"]["approves_merge"])
            self.assertFalse(binding["boundary"]["verifies_evidence"])
            self.assertFalse(binding["boundary"]["executes_commands"])

    def test_proofrun_workflow_plan_missing_or_invalid_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            missing = root / "missing-workflow-plan.json"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "write two proof files",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(missing),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("ERR_ORRO_WORKFLOW_PLAN_LOAD_FAILED", stderr.getvalue())
            self.assertFalse((home / "runs").exists())

            invalid = root / "invalid-workflow-plan.json"
            invalid.write_text(json.dumps({"kind": "not-orro-workflow-plan"}) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "write two proof files",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(invalid),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("ERR_ORRO_WORKFLOW_PLAN_INVALID", stderr.getvalue())

    def test_proofrun_workflow_plan_goal_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = self._flowplan_out(root, "different goal")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "write two proof files",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(plan_path),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("ERR_ORRO_WORKFLOW_PLAN_GOAL_MISMATCH", stderr.getvalue())
            self.assertFalse((home / "runs").exists())

    def test_orro_module_scout_matches_witnessd_orro_public_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _home = self._init_home(root)

            witnessd_scout = self._module_run(
                ["orro", "scout", "inspect repo", "--repo", str(repo)]
            )
            orro_scout = self._orro_module_run(["scout", "inspect repo", "--repo", str(repo)])

            self.assertEqual(witnessd_scout.returncode, 0, witnessd_scout.stderr)
            self.assertEqual(orro_scout.returncode, 0, orro_scout.stderr)
            witnessd_payload = json.loads(witnessd_scout.stdout)
            orro_payload = json.loads(orro_scout.stdout)
            self.assertEqual(set(orro_payload), set(witnessd_payload))
            self.assertEqual(orro_payload["decision"], "scouted")
            self.assertTrue(Path(orro_payload["repo_profile"]).is_file())
            self.assertTrue(Path(orro_payload["context_pack"]).is_file())

    def test_orro_module_flowplan_remains_plan_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)

            flowplan = self._orro_module_run(["flowplan", "plan goal", "--root", str(root)])

            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)
            payload = json.loads(flowplan.stdout)
            self.assertEqual(payload["sealed_plan"]["goal"], "plan goal")
            self.assertNotIn("team_ledger", payload)
            self.assertFalse((root / ".witnessd").exists())

    def test_orro_module_doctor_json_works(self) -> None:
        doctor = self._orro_module_run(["doctor", "--json"])

        self.assertIn(doctor.returncode, {0, 1}, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertEqual(payload["command"], "orro doctor")
        self.assertFalse(payload["boundary"]["executes_recipes"])
        self.assertFalse(payload["boundary"]["raises_assurance"])

    def test_orro_module_help_shows_orro_subcommands(self) -> None:
        help_result = self._orro_module_run(["--help"])

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in (
            "init",
            "scout",
            "flowplan",
            "proofrun",
            "proofcheck",
            "handoff",
            "doctor",
            "engine-lock",
        ):
            self.assertIn(command, help_result.stdout)
        for internal_command in (
            "self-test",
            "team-ledger",
            "lane-exec",
            "a2-observer-run",
            "faultkit",
            "install",
            "upgrade",
        ):
            self.assertNotIn(internal_command, help_result.stdout)

    def test_orro_module_without_args_shows_orro_help(self) -> None:
        help_result = self._orro_module_run([])

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertEqual(help_result.stderr, "")
        self.assertIn("ORRO Flow", help_result.stdout)
        self.assertIn("init", help_result.stdout)
        self.assertIn("engine-lock", help_result.stdout)
        self.assertNotIn("self-test", help_result.stdout)

    def test_witnessd_help_remains_engine_facing(self) -> None:
        witnessd_help = self._module_run(["--help"])

        self.assertEqual(witnessd_help.returncode, 0, witnessd_help.stderr)
        self.assertIn("self-test", witnessd_help.stdout)
        self.assertIn("team-ledger", witnessd_help.stdout)
        self.assertIn("engine-lock", witnessd_help.stdout)

    def test_orro_module_init_delegates_to_witnessd_setup_without_flow_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / ".witnessd"

            init = self._orro_module_run(
                ["init", "--home", str(home), "--depone-root", str(_depone_root())]
            )

            self.assertEqual(init.returncode, 0, init.stderr)
            payload = json.loads(init.stdout)
            self.assertEqual(payload["home"], str(home))
            self.assertTrue((home / "provision.json").is_file())
            self.assertTrue((home / "config.json").is_file())
            self.assertFalse((home / "runs").exists())
            self.assertNotIn("decision", payload)
            self.assertNotIn("assurance", payload)

    def test_witnessd_orro_init_alias_delegates_to_witnessd_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / ".witnessd"

            init = self._module_run(
                ["orro", "init", "--home", str(home), "--depone-root", str(_depone_root())]
            )

            self.assertEqual(init.returncode, 0, init.stderr)
            payload = json.loads(init.stdout)
            self.assertEqual(payload["home"], str(home))
            provision = json.loads((home / "provision.json").read_text(encoding="utf-8"))
            self.assertEqual(provision["kind"], "witnessd-depone-provision")

    def test_orro_public_setup_smoke_reaches_engine_lock_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / ".witnessd"
            lock_path = root / "orro-engine-lock.json"

            init = self._orro_module_run(
                ["init", "--home", str(home), "--depone-root", str(_depone_root())]
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((home / "provision.json").is_file())

            doctor = self._orro_module_run(["doctor", "--home", str(home), "--json"])
            self.assertIn(doctor.returncode, {0, 1}, doctor.stderr)
            doctor_payload = json.loads(doctor.stdout)
            self.assertEqual(doctor_payload["command"], "orro doctor")
            self.assertFalse(doctor_payload["boundary"]["verifier_refuted"])
            self.assertFalse(doctor_payload["boundary"]["raises_assurance"])

            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(lock_path)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)

            check_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--check", str(lock_path), "--json"]
            )
            self.assertEqual(check_lock.returncode, 0, check_lock.stderr)
            self.assertTrue(json.loads(check_lock.stdout)["locked"])
            self.assertFalse((home / "runs").exists())

    def test_orro_engine_lock_writes_distribution_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "orro-engine-lock.json"

            lock = self._orro_module_run(["engine-lock", "--home", str(home), "--out", str(out)])

            self.assertEqual(lock.returncode, 0, lock.stderr)
            payload = json.loads(lock.stdout)
            self.assertEqual(payload["kind"], "orro-engine-lock")
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(payload, json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual(payload["witnessd"]["repository"], "Moonweave-Systems/witnessd")
            self.assertRegex(payload["witnessd"]["commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(payload["depone"]["repository"], "Moonweave-Systems/Depone")
            self.assertRegex(payload["depone"]["commit"], r"^[0-9a-f]{40}$")
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])
            self.assertFalse(payload["boundary"]["executes_commands"])
            self.assertFalse(payload["boundary"]["verifies_evidence"])
            self.assertFalse((home / "runs").exists())

    def test_witnessd_orro_engine_lock_alias_writes_distribution_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "lock.json"

            lock = self._module_run(
                ["orro", "engine-lock", "--home", str(home), "--out", str(out)]
            )

            self.assertEqual(lock.returncode, 0, lock.stderr)
            payload = json.loads(lock.stdout)
            self.assertEqual(payload["kind"], "orro-engine-lock")
            self.assertTrue(out.is_file())

    def test_orro_engine_lock_check_accepts_matching_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "orro-engine-lock.json"
            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)

            check = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--check", str(out), "--json"]
            )

            self.assertEqual(check.returncode, 0, check.stderr)
            payload = json.loads(check.stdout)
            self.assertEqual(payload["command"], "orro engine-lock check")
            self.assertTrue(payload["locked"])
            self.assertEqual(payload["mismatches"], [])
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])
            self.assertFalse(payload["boundary"]["executes_commands"])
            self.assertFalse(payload["boundary"]["verifies_evidence"])
            self.assertFalse((home / "runs").exists())

    def test_witnessd_orro_engine_lock_alias_checks_matching_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "lock.json"
            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)

            check = self._module_run(
                ["orro", "engine-lock", "--home", str(home), "--check", str(out), "--json"]
            )

            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertTrue(json.loads(check.stdout)["locked"])

    def test_orro_engine_lock_check_reports_mismatches_without_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "lock.json"
            bad_lock = root / "bad-lock.json"
            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            payload["witnessd"]["commit"] = "0" * 40
            bad_lock.write_text(json.dumps(payload), encoding="utf-8")

            check = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--check", str(bad_lock), "--json"]
            )

            self.assertEqual(check.returncode, 1)
            result = json.loads(check.stdout)
            self.assertFalse(result["locked"])
            self.assertEqual(result["error"]["code"], "ERR_ORRO_ENGINE_LOCK_MISMATCH")
            mismatch_fields = {entry["field"] for entry in result["mismatches"]}
            self.assertIn("witnessd.commit", mismatch_fields)
            commit_mismatch = next(
                entry for entry in result["mismatches"] if entry["field"] == "witnessd.commit"
            )
            self.assertEqual(commit_mismatch["expected"], "0" * 40)
            self.assertRegex(commit_mismatch["current"], r"^[0-9a-f]{40}$")
            self.assertFalse(result["boundary"]["raises_assurance"])
            self.assertFalse(result["boundary"]["verifies_evidence"])

    def test_orro_engine_lock_check_fails_closed_for_invalid_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            cases = {
                "wrong-kind": json.dumps({"kind": "not-orro-engine-lock"}),
                "wrong-schema": json.dumps(
                    {"kind": "orro-engine-lock", "schema_version": "9.9"}
                ),
                "malformed": "{not json",
                "non-object": "[]",
            }
            for name, contents in cases.items():
                with self.subTest(name=name):
                    invalid = root / f"{name}-lock.json"
                    invalid.write_text(contents, encoding="utf-8")

                    check = self._orro_module_run(
                        ["engine-lock", "--home", str(home), "--check", str(invalid), "--json"]
                    )

                    self.assertEqual(check.returncode, 2)
                    payload = json.loads(check.stdout)
                    self.assertFalse(payload["locked"])
                    self.assertIn(
                        payload["error"]["code"],
                        {
                            "ERR_ORRO_ENGINE_LOCK_INVALID",
                            "ERR_ORRO_ENGINE_LOCK_LOAD_FAILED",
                        },
                    )

    def test_orro_engine_lock_check_fails_closed_for_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            missing = root / "missing-lock.json"

            check = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--check", str(missing), "--json"]
            )

            self.assertEqual(check.returncode, 2)
            payload = json.loads(check.stdout)
            self.assertFalse(payload["locked"])
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_ENGINE_LOCK_LOAD_FAILED")

    def test_orro_engine_lock_blocks_on_uninitialized_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            out = root / "orro-engine-lock.json"

            lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out), "--json"]
            )

            self.assertEqual(lock.returncode, 2)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(lock.stdout)["error"]["code"],
                "ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING",
            )

    def test_orro_engine_lock_blocks_on_malformed_provision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            (home / "provision.json").write_text("{not json", encoding="utf-8")

            lock = self._orro_module_run(["engine-lock", "--home", str(home), "--json"])

            self.assertEqual(lock.returncode, 2)
            self.assertEqual(
                json.loads(lock.stdout)["error"]["code"],
                "ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING",
            )

    def test_orro_engine_lock_blocks_on_non_object_provision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            (home / "provision.json").write_text("[]", encoding="utf-8")

            lock = self._orro_module_run(["engine-lock", "--home", str(home), "--json"])

            self.assertEqual(lock.returncode, 2)
            self.assertEqual(
                json.loads(lock.stdout)["error"]["code"],
                "ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING",
            )

    def test_orro_engine_lock_requires_home(self) -> None:
        lock = self._orro_module_run(["engine-lock", "--json"])

        self.assertEqual(lock.returncode, 2)
        self.assertEqual(
            json.loads(lock.stdout)["error"]["code"],
            "ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
        )

    def test_proofcheck_delegates_team_ledger_run_dir_to_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            out = run_dir / "proofcheck-verdict.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["command"], "proofcheck")
            self.assertEqual(payload["verifier_command"], "team-ledger")
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["out"], str(out))
            self.assertEqual(payload["orro_binding"]["kind"], "orro-proofcheck-binding")
            self.assertTrue(out.is_file())

    def test_proofcheck_preserves_workflow_plan_binding_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            home, run_dir, _payload = self._proofrun(root, workflow_plan=plan_path)
            out = run_dir / "proofcheck-verdict.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertIn("workflow_plan", payload)
            self.assertEqual(payload["workflow_plan"]["path"], str(run_dir / "workflow-plan.json"))
            verdict_payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(verdict_payload["workflow_plan"], payload["workflow_plan"])
            self.assertFalse(verdict_payload["workflow_plan"]["boundary"]["raises_assurance"])

    def test_proofcheck_without_out_does_not_write_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            verdict = run_dir / "proofcheck-verdict.json"
            if verdict.exists():
                verdict.unlink()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertNotIn("out", payload)
            self.assertFalse(verdict.exists())

    def test_proofcheck_out_fails_closed_when_depone_writes_no_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            out = evidence_dir / "proofcheck-verdict.json"

            with patch(
                "witnessd.__main__._run_depone_json",
                return_value=(
                    0,
                    {
                        "decision": "pass",
                        "verifier_command": "proofcheck",
                        "out": str(out),
                    },
                ),
            ):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(["proofcheck", str(evidence_dir), "--out", str(out)])

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
            )
            self.assertNotIn("orro_binding", payload)

    def test_proofcheck_out_fails_closed_when_depone_writes_bad_verdict(self) -> None:
        for contents in ("{not json\n", "[]\n"):
            with self.subTest(contents=contents):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    evidence_dir = root / "evidence"
                    evidence_dir.mkdir()
                    out = evidence_dir / "proofcheck-verdict.json"

                    def fake_depone(_command: list[str], *, env: dict[str, str]) -> tuple[int, dict]:
                        out.write_text(contents, encoding="utf-8")
                        return (
                            0,
                            {
                                "decision": "pass",
                                "verifier_command": "proofcheck",
                                "out": str(out),
                            },
                        )

                    with patch("witnessd.__main__._run_depone_json", side_effect=fake_depone):
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            code = main(["proofcheck", str(evidence_dir), "--out", str(out)])

                    self.assertEqual(code, 1)
                    payload = json.loads(stdout.getvalue())
                    self.assertEqual(payload["decision"], "blocked")
                    self.assertEqual(
                        payload["error"]["code"],
                        "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
                    )
                    self.assertNotIn("orro_binding", payload)

    def test_orro_proofcheck_blocks_scout_only_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _home = self._init_home(root)
            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect", "--repo", str(repo)]), 0)
            scout_dir = Path(json.loads(scout_stdout.getvalue())["run_dir"])

            proofcheck_stdout = io.StringIO()
            with redirect_stdout(proofcheck_stdout):
                code = main(["orro", "proofcheck", str(scout_dir)])

            self.assertEqual(code, 1)
            payload = json.loads(proofcheck_stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["verifier_command"], "proofcheck")

    def test_orro_handoff_hashes_evidence_without_approval_or_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(run_dir / "proofcheck-verdict.json")]),
                    0,
                )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json")])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-handoff")
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])
            proofcheck_payload = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                proofcheck_payload["orro_binding"]["artifact_hashes"],
                payload["artifact_hashes"],
            )
            hashed_paths = {item["path"] for item in payload["artifact_hashes"]}
            self.assertIn("team-ledger.json", hashed_paths)
            self.assertNotIn("proofcheck-verdict.json", hashed_paths)
            self.assertNotIn("team-ledger-verdict.json", hashed_paths)
            self.assertNotIn("orro-handoff.json", hashed_paths)
            self.assertTrue((run_dir / "orro-handoff.json").is_file())

            rerun_stdout = io.StringIO()
            with redirect_stdout(rerun_stdout):
                self.assertEqual(
                    main(
                        [
                            "orro",
                            "handoff",
                            str(run_dir),
                            "--out",
                            str(run_dir / "orro-handoff.json"),
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                json.loads(rerun_stdout.getvalue())["artifact_hashes"],
                payload["artifact_hashes"],
            )

    def test_handoff_includes_workflow_plan_binding_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            home, run_dir, _payload = self._proofrun(root, workflow_plan=plan_path)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "proofcheck",
                            str(run_dir),
                            "--home",
                            str(home),
                            "--out",
                            str(run_dir / "proofcheck-verdict.json"),
                        ]
                    ),
                    0,
                )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json")])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertIn("workflow_plan", payload)
            self.assertEqual(payload["workflow_plan"]["path"], str(run_dir / "workflow-plan.json"))
            self.assertFalse(payload["workflow_plan"]["boundary"]["raises_assurance"])
            written = json.loads((run_dir / "orro-handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(written["workflow_plan"], payload["workflow_plan"])

    def test_orro_handoff_requires_explicit_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_REQUIRED",
            )

    def test_handoff_rejects_malformed_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text("{not json\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            )

    def test_handoff_rejects_non_object_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text("[]\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            )

    def test_orro_handoff_rejects_non_passing_proofcheck_verdict(self) -> None:
        for decision in ("blocked", "refuted"):
            with self.subTest(decision=decision):
                with tempfile.TemporaryDirectory() as tmp:
                    _home, run_dir, _payload = self._proofrun(Path(tmp))
                    (run_dir / "proofcheck-verdict.json").write_text(
                        json.dumps({"decision": decision}),
                        encoding="utf-8",
                    )
                    out = run_dir / "orro-handoff.json"

                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists())
                    self.assertEqual(
                        json.loads(stdout.getvalue())["error"]["code"],
                        "ERR_ORRO_HANDOFF_PROOFCHECK_NOT_PASS",
                    )

    def test_handoff_rejects_unbound_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text(
                json.dumps({"decision": "pass"}),
                encoding="utf-8",
            )
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_UNBOUND",
            )

    def test_handoff_rejects_stale_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            home, first_run_dir, _payload = self._proofrun(first_root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "proofcheck",
                            str(first_run_dir),
                            "--home",
                            str(home),
                            "--out",
                            str(first_run_dir / "proofcheck-verdict.json"),
                        ]
                    ),
                    0,
                )
            _home, second_run_dir, _payload = self._proofrun(second_root)
            (second_run_dir / "proofcheck-verdict.json").write_text(
                (first_run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            out = second_run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(second_run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_BINDING_MISMATCH",
            )

    def test_handoff_ignores_non_object_optional_decision_ref_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "proofcheck",
                            str(run_dir),
                            "--home",
                            str(home),
                            "--out",
                            str(run_dir / "proofcheck-verdict.json"),
                        ]
                    ),
                    0,
                )
            (run_dir / "team-ledger-verdict.json").write_text("[]\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            team_ref = next(
                ref
                for ref in payload["decision_refs"]
                if ref["path"] == "team-ledger-verdict.json"
            )
            self.assertNotIn("decision", team_ref)
            self.assertTrue(out.is_file())

    def test_public_orro_json_errors_are_json(self) -> None:
        proofcheck_stdout = io.StringIO()
        with redirect_stdout(proofcheck_stdout):
            proofcheck_code = main(["proofcheck", "--json"])

        self.assertEqual(proofcheck_code, 2)
        self.assertEqual(
            json.loads(proofcheck_stdout.getvalue())["error"]["code"],
            "ERR_ORRO_PROOFCHECK_INPUT_REQUIRED",
        )

        handoff_stdout = io.StringIO()
        with redirect_stdout(handoff_stdout):
            handoff_code = main(["handoff", "--json"])

        self.assertEqual(handoff_code, 2)
        self.assertEqual(
            json.loads(handoff_stdout.getvalue())["error"]["code"],
            "ERR_ORRO_HANDOFF_INPUT_REQUIRED",
        )

    def test_proofcheck_json_pin_failure_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            (evidence_dir / "team-ledger.json").write_text("{}", encoding="utf-8")
            bad_home = root / "uninitialized-home"
            bad_home.mkdir()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "proofcheck",
                        str(evidence_dir),
                        "--home",
                        str(bad_home),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_WITNESSD_DEPONE_PIN_MISSING",
            )

    def test_orro_doctor_reports_readiness_not_verifier_refutation(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "doctor", "--adapter", "codex"])

        self.assertIn(code, {0, 1})
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["command"], "orro doctor")
        self.assertFalse(payload["boundary"]["verifier_refuted"])
        self.assertFalse(payload["boundary"]["raises_assurance"])

    def test_orro_doctor_blocks_invalid_depone_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / "provision.json").write_text(
                json.dumps(
                    {
                        "kind": "witnessd-depone-provision",
                        "schema_version": "0.1",
                        "depone": {
                            "root": str(Path(tmp) / "missing-depone"),
                            "commit": "0" * 40,
                        },
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "doctor", "--home", str(home), "--json"])

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(checks["depone_pin"]["status"], "blocked")
            self.assertEqual(
                checks["depone_pin"]["code"],
                "ERR_WITNESSD_DEPONE_ROOT_INVALID",
            )
            self.assertFalse(payload["boundary"]["verifier_refuted"])

    def test_orro_doctor_reports_matching_engine_lock_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "lock.json"
            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--engine-lock",
                        str(out),
                        "--adapter",
                        "codex",
                        "--json",
                    ]
                )

            self.assertIn(code, {0, 1})
            payload = json.loads(stdout.getvalue())
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(checks["engine_lock"]["status"], "pass")
            self.assertTrue(checks["engine_lock"]["locked"])
            self.assertFalse(payload["boundary"]["verifier_refuted"])

    def test_orro_doctor_blocks_mismatched_engine_lock_as_readiness_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            out = root / "lock.json"
            write_lock = self._orro_module_run(
                ["engine-lock", "--home", str(home), "--out", str(out)]
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            payload["depone"]["commit"] = "f" * 40
            out.write_text(json.dumps(payload), encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--engine-lock",
                        str(out),
                        "--adapter",
                        "codex",
                        "--json",
                    ]
                )

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(checks["engine_lock"]["status"], "blocked")
            self.assertEqual(
                checks["engine_lock"]["code"],
                "ERR_ORRO_ENGINE_LOCK_MISMATCH",
            )
            self.assertFalse(checks["engine_lock"]["locked"])
            self.assertFalse(payload["boundary"]["verifier_refuted"])

    def test_full_orro_flow_module_surface_reaches_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)

            scout = self._module_run(
                ["orro", "scout", "inspect repo", "--repo", str(repo), "--home", str(home)]
            )
            self.assertEqual(scout.returncode, 0, scout.stderr)
            scout_payload = json.loads(scout.stdout)
            self.assertEqual(scout_payload["decision"], "scouted")

            flowplan = self._module_run(
                ["orro", "flowplan", "plan proof run", "--root", str(repo)]
            )
            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)
            flowplan_payload = json.loads(flowplan.stdout)
            self.assertEqual(flowplan_payload["sealed_plan"]["goal"], "plan proof run")
            self.assertNotIn("team_ledger", flowplan_payload)

            proofrun = self._module_run(
                [
                    "orro",
                    "proofrun",
                    "write proof files",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                ]
            )
            self.assertEqual(proofrun.returncode, 0, proofrun.stderr)
            proofrun_payload = json.loads(proofrun.stdout)
            run_dir = Path(proofrun_payload["run_dir"])
            self.assertTrue((run_dir / "team-ledger.json").is_file())

            proofcheck = self._module_run(
                [
                    "orro",
                    "proofcheck",
                    str(run_dir),
                    "--home",
                    str(home),
                    "--out",
                    str(run_dir / "proofcheck-verdict.json"),
                ]
            )
            self.assertEqual(proofcheck.returncode, 0, proofcheck.stderr)
            proofcheck_payload = json.loads(proofcheck.stdout)
            self.assertEqual(proofcheck_payload["decision"], "pass")
            self.assertEqual(proofcheck_payload["verifier_command"], "team-ledger")
            self.assertEqual(proofcheck_payload["out"], str(run_dir / "proofcheck-verdict.json"))

            handoff = self._module_run(
                [
                    "orro",
                    "handoff",
                    str(run_dir),
                    "--out",
                    str(run_dir / "orro-handoff.json"),
                ]
            )
            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            handoff_payload = json.loads(handoff.stdout)
            self.assertEqual(handoff_payload["kind"], "orro-handoff")
            self.assertFalse(handoff_payload["boundary"]["approves_merge"])
            self.assertFalse(handoff_payload["boundary"]["raises_assurance"])


if __name__ == "__main__":
    unittest.main()
