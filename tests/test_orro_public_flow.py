from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.orro_report import build_report
from witnessd.orro_team_surface import apply_task_prompt_to_role_lane_plan
from witnessd.signing import gen_operator_keypair


TRUSTED_OBSERVER_PUBLIC_KEY_ENV = "DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"


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

    def _role_lane_plan_out(
        self,
        root: Path,
        goal: str,
        *,
        profile: str = "code-change",
        explicit_prompt: bool = True,
    ) -> Path:
        out = root / "role-lane-plan.json"
        rolepack = _write_shell_rolepack(root) if profile == "code-change" else None
        rolepack_args = (
            ["--rolepack-file", str(rolepack)] if rolepack is not None else []
        )
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
                    "--role-lanes-out",
                    str(out),
                    *rolepack_args,
                ]
            )
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(out.is_file())
        if explicit_prompt:
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
        orro_alias: bool = False,
        workflow_plan: Path | None = None,
        role_lane_plan: Path | None = None,
        external_keys_dir: Path | None = None,
    ) -> tuple[Path, Path, dict]:
        repo, home = self._init_home(root)
        trusted_public_key: Path | None = None
        if external_keys_dir is not None:
            external_keys_dir.mkdir()
            private_key, public_key = gen_operator_keypair(str(external_keys_dir))
            home_keys = home / "keys"
            shutil.copyfile(private_key, home_keys / "operator-ed25519.pem")
            shutil.copyfile(public_key, home_keys / "operator-ed25519.pub.pem")
            trusted_public_key = Path(public_key)
        stdout = io.StringIO()
        stderr = io.StringIO()
        command = ["orro", "proofrun"] if orro_alias else ["proofrun"]
        args = [
            *command,
            "write two proof files",
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--max-parallel",
            "1",
        ]
        if workflow_plan is not None:
            args.extend(["--workflow-plan", str(workflow_plan)])
        if role_lane_plan is not None:
            args.extend(["--role-lane-plan", str(role_lane_plan)])
        if role_lane_plan is None:
            args.append("--allow-reference-adapter")
        with (
            patch.dict(os.environ, {}, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            if trusted_public_key is None:
                os.environ.pop(TRUSTED_OBSERVER_PUBLIC_KEY_ENV, None)
            else:
                os.environ[TRUSTED_OBSERVER_PUBLIC_KEY_ENV] = str(trusted_public_key)
            code = main(args)
        self.assertEqual(code, 0, f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
        payload = json.loads(stdout.getvalue())
        return home, Path(payload["run_dir"]), payload

    def _proofcheck_out(
        self,
        home: Path,
        run_dir: Path,
        *,
        trusted_public_key: Path | None = None,
    ) -> dict:
        out = run_dir / "proofcheck-verdict.json"
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), redirect_stdout(stdout):
            if trusted_public_key is None:
                os.environ.pop(TRUSTED_OBSERVER_PUBLIC_KEY_ENV, None)
            else:
                os.environ[TRUSTED_OBSERVER_PUBLIC_KEY_ENV] = str(trusted_public_key)
            code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(out.is_file())
        return json.loads(stdout.getvalue())

    def _emit_sketch_bundle(self, root: Path, home: Path, run_dir: Path) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "orro",
                    "sketch",
                    "seal one bounded advisory direction",
                    "--repo",
                    str(root / "repo"),
                    "--home",
                    str(home),
                    "--out",
                    str(run_dir / "orro-sketch.json"),
                    "--json",
                ]
            )
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue((run_dir / "advisory-provenance-bundle.json").is_file())
        self.assertTrue((run_dir / "evidence-contract.json").is_file())

    def _verify_out(
        self,
        home: Path,
        run_dir: Path,
        *,
        trusted_public_key: Path | None = None,
    ) -> dict:
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), redirect_stdout(stdout):
            if trusted_public_key is None:
                os.environ.pop(TRUSTED_OBSERVER_PUBLIC_KEY_ENV, None)
            else:
                os.environ[TRUSTED_OBSERVER_PUBLIC_KEY_ENV] = str(trusted_public_key)
            code = main(["verify", str(run_dir), "--home", str(home)])
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def _handoff_out(self, run_dir: Path) -> dict:
        out = run_dir / "orro-handoff.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "handoff", str(run_dir), "--out", str(out)])
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(out.is_file())
        return json.loads(stdout.getvalue())

    def _orro_next(self, run_dir: Path, home: Path, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "next", str(run_dir), "--home", str(home), "--json", *extra])
        return code, json.loads(stdout.getvalue())

    def _orro_auto_dry_run(self, run_dir: Path, home: Path, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "auto", "--dry-run", str(run_dir), "--home", str(home), "--json", *extra])
        return code, json.loads(stdout.getvalue())

    def _orro_auto_once(self, run_dir: Path, home: Path, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "auto", "--once", str(run_dir), "--home", str(home), "--json", *extra])
        return code, json.loads(stdout.getvalue())

    def _orro_auto_until_complete(self, run_dir: Path, home: Path, *extra: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                ["orro", "auto", "--until-complete", str(run_dir), "--home", str(home), "--json", *extra]
            )
        return code, json.loads(stdout.getvalue())

    def test_proofrun_alias_reuses_run_surface_without_final_trust_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, payload = self._proofrun(Path(tmp))

            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["trust_anchor"], "self-signed")
            self.assertFalse(payload["independent_trust_anchor"])
            self.assertTrue((run_dir / "team-ledger.json").is_file())
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())
            self.assertNotIn("final_trust", payload)
            self.assertNotIn("raises_assurance", payload)
            self.assertNotIn("assurance", payload)

            verify = self._verify_out(home, run_dir)
            self.assertEqual(verify["decision"], "pass")
            self.assertEqual(verify["trust_anchor"], "self-signed")
            self.assertFalse(verify["independent_trust_anchor"])
            self.assertNotIn("assurance", verify)

            proofcheck = self._proofcheck_out(home, run_dir)
            self.assertEqual(proofcheck["decision"], "pass")
            self.assertEqual(proofcheck["trust_anchor"], "self-signed")
            self.assertFalse(proofcheck["independent_trust_anchor"])
            self.assertNotIn("assurance", proofcheck)
            self.assertNotIn("advisory_provenance", proofcheck)
            verdict = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verdict["trust_anchor"], "self-signed")
            self.assertFalse(verdict["independent_trust_anchor"])
            self.assertNotIn("assurance", verdict)

    def test_external_operator_key_unlocks_independent_trust_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external_keys = root / "operator-controlled-keys"
            home, run_dir, payload = self._proofrun(
                root,
                external_keys_dir=external_keys,
            )
            trusted_public_key = external_keys / "operator-ed25519.pub.pem"

            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["trust_anchor"], "operator-provided")
            self.assertTrue(payload["independent_trust_anchor"])

            verify = self._verify_out(
                home,
                run_dir,
                trusted_public_key=trusted_public_key,
            )
            self.assertEqual(verify["decision"], "pass")
            self.assertEqual(verify["trust_anchor"], "operator-provided")
            self.assertTrue(verify["independent_trust_anchor"])

            proofcheck = self._proofcheck_out(
                home,
                run_dir,
                trusted_public_key=trusted_public_key,
            )
            self.assertEqual(proofcheck["decision"], "pass")
            self.assertEqual(proofcheck["trust_anchor"], "operator-provided")
            self.assertTrue(proofcheck["independent_trust_anchor"])
            verdict = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verdict["trust_anchor"], "operator-provided")
            self.assertTrue(verdict["independent_trust_anchor"])

    def test_orro_proofrun_normalizes_to_proofrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, payload = self._proofrun(Path(tmp), orro_alias=True)

            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger.json").is_file())

    def test_orro_proofrun_without_plan_fails_closed_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "make a real change",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_PROOFRUN_NO_PLAN")
            self.assertFalse((home / "runs").exists())
            self.assertFalse(any(home.rglob("team-ledger-verdict.json")))
            self.assertFalse(any(home.rglob("proofcheck-verdict.json")))

    def test_orro_proofrun_reference_opt_in_marks_all_outputs_not_real_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "make a real change",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--allow-reference-adapter",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            run_dir = Path(payload["run_dir"])
            ledger = json.loads((run_dir / "team-ledger.json").read_text(encoding="utf-8"))
            team_verdict = json.loads(
                (run_dir / "team-ledger-verdict.json").read_text(encoding="utf-8")
            )
            proofcheck = self._proofcheck_out(home, run_dir)
            proofcheck_verdict = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            report_code, report = build_report(run_dir, home=home)

            self.assertEqual(report_code, 0)
            for artifact in (
                payload,
                ledger,
                team_verdict,
                proofcheck,
                proofcheck_verdict,
            ):
                self.assertTrue(artifact["not_real_ai_work"])
                self.assertTrue(artifact["placeholder_fallback"])
            self.assertTrue(report["not_real_ai_work"])
            self.assertTrue(report["placeholder_fallback"])
            self.assertTrue(report["summary"]["not_real_ai_work"])
            self.assertTrue(report["summary"]["placeholder_fallback"])
            self.assertTrue(report["reference_adapter"]["not_real_ai_work"])
            self.assertTrue(report["reference_adapter"]["placeholder_fallback"])
            self.assertIn("not real AI work", report["summary"]["headline"])

    def test_orro_proofrun_workflow_without_role_lanes_requires_reference_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = self._flowplan_out(root, "make a real change")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "make a real change",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(plan_path),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_PROOFRUN_NO_PLAN")
            self.assertFalse((home / "runs").exists())

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

    def test_proofrun_workflow_plan_writes_role_dispatch_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files", profile="code-change")

            _home, run_dir, payload = self._proofrun(root, workflow_plan=plan_path)

            self.assertIn("workflow_role_dispatch", payload)
            dispatch_ref = payload["workflow_role_dispatch"]
            self.assertEqual(dispatch_ref["path"], str(run_dir / "workflow-role-dispatch.json"))
            self.assertRegex(dispatch_ref["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(dispatch_ref["profile"], "code-change")
            dispatch = json.loads((run_dir / "workflow-role-dispatch.json").read_text(encoding="utf-8"))
            self.assertEqual(dispatch["kind"], "orro-role-dispatch")
            self.assertEqual(dispatch["workflow_plan_hash"], payload["workflow_plan"]["sha256"])
            self.assertFalse(dispatch["boundary"]["role_dispatch_is_proof"])
            self.assertFalse(dispatch["boundary"]["raises_assurance"])
            self.assertFalse(dispatch["boundary"]["approves_merge"])
            runner = next(role for role in dispatch["roles"] if role["phase"] == "proofrun")
            self.assertEqual(runner["status"], "executed")
            self.assertIn("team-ledger.json", runner["evidence_refs"])
            self.assertEqual(sorted(runner["lane_ids"]), ["w18-lane-a", "w18-lane-b"])
            verifier = next(role for role in dispatch["roles"] if role["phase"] == "proofcheck")
            self.assertEqual(verifier["status"], "pending-proofcheck")
            self.assertFalse(verifier["may_execute"])
            self.assertTrue(verifier["may_verify"])

    def test_proofrun_role_lane_plan_executes_declared_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files", profile="code-change")
            role_lane_path = self._role_lane_plan_out(root, "write two proof files", profile="code-change")

            _home, run_dir, payload = self._proofrun(
                root,
                workflow_plan=plan_path,
                role_lane_plan=role_lane_path,
            )

            for name in (
                "workflow-plan.json",
                "workflow-plan-binding.json",
                "role-lane-plan.json",
                "role-lane-plan-binding.json",
                "workflow-role-dispatch.json",
                "team-ledger.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            self.assertIn("role_lane_plan", payload)
            role_lane_ref = payload["role_lane_plan"]
            self.assertEqual(role_lane_ref["path"], str(run_dir / "role-lane-plan.json"))
            self.assertEqual(role_lane_ref["binding_path"], str(run_dir / "role-lane-plan-binding.json"))
            self.assertRegex(role_lane_ref["sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(role_lane_ref["boundary"]["role_lane_plan_is_proof"])
            self.assertFalse(role_lane_ref["boundary"]["raises_assurance"])
            self.assertFalse(role_lane_ref["boundary"]["approves_merge"])

            role_lane_plan = json.loads((run_dir / "role-lane-plan.json").read_text(encoding="utf-8"))
            lane_ids = sorted(lane["lane_id"] for lane in role_lane_plan["lanes"])
            ledger = json.loads((run_dir / "team-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(lane["lane_id"] for lane in ledger["lanes"]), lane_ids)
            dispatch = json.loads((run_dir / "workflow-role-dispatch.json").read_text(encoding="utf-8"))
            self.assertEqual(dispatch["role_lane_plan_hash"], role_lane_ref["sha256"])
            runner = next(role for role in dispatch["roles"] if role["phase"] == "proofrun")
            self.assertEqual(sorted(runner["lane_ids"]), lane_ids)
            self.assertIn("role-lane-plan.json", runner["evidence_refs"])
            self.assertIn("team-ledger.json", runner["evidence_refs"])
            self.assertFalse(dispatch["boundary"]["role_dispatch_is_proof"])
            self.assertFalse(dispatch["boundary"]["raises_assurance"])
            self.assertFalse(dispatch["boundary"]["approves_merge"])

    def test_proofrun_refuses_placeholder_role_lane_prompt_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = self._flowplan_out(root, "write two proof files")
            role_lane_path = self._role_lane_plan_out(
                root,
                "write two proof files",
                explicit_prompt=False,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
                        "--role-lane-plan",
                        str(role_lane_path),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT",
            )
            self.assertFalse((home / "runs").exists())

    def test_orro_next_after_proofrun_needs_proofcheck_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            role_lane_path = self._role_lane_plan_out(root, "write two proof files")
            home, run_dir, _payload = self._proofrun(
                root,
                workflow_plan=plan_path,
                role_lane_plan=role_lane_path,
            )
            before = sorted(path.relative_to(run_dir) for path in run_dir.rglob("*"))

            code, payload = self._orro_next(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["kind"], "orro-continuation-decision")
            self.assertEqual(payload["decision"], "needs-proofcheck")
            self.assertFalse(payload["blocked"])
            self.assertIn("orro proofcheck", payload["next_allowed"][0])
            self.assertTrue(payload["observed_artifacts"]["team_ledger"])
            self.assertFalse(payload["observed_artifacts"]["proofcheck_verdict"])
            runner = next(role for role in payload["role_status"] if role["phase"] == "proofrun")
            verifier = next(role for role in payload["role_status"] if role["phase"] == "proofcheck")
            self.assertEqual(runner["status"], "executed")
            self.assertEqual(verifier["status"], "pending")
            self.assertFalse(payload["boundary"]["executes_commands"])
            self.assertFalse(payload["boundary"]["verifies_evidence"])
            self.assertFalse(payload["boundary"]["raises_assurance"])
            after = sorted(path.relative_to(run_dir) for path in run_dir.rglob("*"))
            self.assertEqual(after, before)

    def test_orro_next_after_passing_proofcheck_is_ready_for_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)

            code, payload = self._orro_next(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["decision"], "ready-for-handoff")
            self.assertFalse(payload["blocked"])
            self.assertIn("orro handoff", payload["next_allowed"][0])
            self.assertIn(f"--home {home}", payload["next_allowed"][0])
            verifier = next(role for role in payload["role_status"] if role["phase"] == "proofcheck")
            handoff = next(role for role in payload["role_status"] if role["phase"] == "handoff")
            self.assertEqual(verifier["status"], "verified")
            self.assertEqual(handoff["status"], "pending")

    def test_orro_next_after_handoff_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            self._handoff_out(run_dir)

            code, payload = self._orro_next(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["decision"], "complete")
            self.assertEqual(payload["next_allowed"], [])
            handoff = next(role for role in payload["role_status"] if role["phase"] == "handoff")
            self.assertEqual(handoff["status"], "packaged")

    def test_orro_next_blocks_stale_handoff_before_complete_or_auto_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root = root / "first"
            first_root.mkdir()
            first_home, first_run_dir, _payload = self._proofrun(first_root)
            self._proofcheck_out(first_home, first_run_dir)
            self._handoff_out(first_run_dir)

            second_root = root / "second"
            second_root.mkdir()
            second_home, second_run_dir, _payload = self._proofrun(second_root)
            self._proofcheck_out(second_home, second_run_dir)
            (second_run_dir / "orro-handoff.json").write_text(
                (first_run_dir / "orro-handoff.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            code, payload = self._orro_next(second_run_dir, second_home)
            self.assertEqual(code, 1)
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_NEXT_HANDOFF_BINDING_MISMATCH")

            auto_code, auto_payload = self._orro_auto_dry_run(second_run_dir, second_home)
            self.assertEqual(auto_code, 1)
            self.assertEqual(auto_payload["would_run"], [])
            self.assertTrue(auto_payload["blocked"])

    def test_orro_next_blocks_non_pass_unbound_and_stale_proofcheck_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            verdict_path = run_dir / "proofcheck-verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

            cases = {
                "non-pass": {**verdict, "decision": "fail"},
                "unbound": {key: value for key, value in verdict.items() if key != "orro_binding"},
            }
            for name, payload in cases.items():
                with self.subTest(name=name):
                    verdict_path.write_text(json.dumps(payload), encoding="utf-8")
                    code, decision = self._orro_next(run_dir, home)
                    self.assertEqual(code, 1)
                    self.assertEqual(decision["decision"], "blocked")
                    self.assertTrue(decision["blocked"])
                    self.assertIn(decision["error"]["code"], {
                        "ERR_ORRO_NEXT_PROOFCHECK_NOT_PASS",
                        "ERR_ORRO_NEXT_PROOFCHECK_UNBOUND",
                    })

            other_root = root / "other"
            other_root.mkdir()
            other_home, other_run_dir, _other_payload = self._proofrun(other_root)
            self._proofcheck_out(other_home, other_run_dir)
            verdict_path.write_text(
                (other_run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            code, decision = self._orro_next(run_dir, home)
            self.assertEqual(code, 1)
            self.assertEqual(decision["decision"], "blocked")
            self.assertEqual(decision["error"]["code"], "ERR_ORRO_NEXT_PROOFCHECK_BINDING_MISMATCH")

    def test_orro_next_invalid_and_scout_only_dirs_do_not_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            missing = root / "missing-run"

            code, payload = self._orro_next(missing, home)
            self.assertEqual(code, 2)
            self.assertEqual(payload["decision"], "invalid-run-dir")
            self.assertTrue(payload["blocked"])

            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect repo", "--repo", str(repo)]), 0)
            scout_payload = json.loads(scout_stdout.getvalue())
            scout_dir = Path(scout_payload["context_pack"]).parent

            code, payload = self._orro_next(scout_dir, home)
            self.assertEqual(code, 1)
            self.assertIn(payload["decision"], {"blocked", "evidence-pending"})
            self.assertNotIn(payload["decision"], {"ready-for-handoff", "complete"})

    def test_orro_next_out_writes_same_decision_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            out = run_dir / "orro-continuation-decision.json"

            code, payload = self._orro_next(run_dir, home, "--out", str(out))

            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)

    def test_orro_auto_dry_run_after_proofrun_plans_proofcheck_without_running_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            next_code, next_payload = self._orro_next(run_dir, home)
            self.assertEqual(next_code, 0)
            self.assertEqual(next_payload["decision"], "needs-proofcheck")

            code, payload = self._orro_auto_dry_run(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["kind"], "orro-auto-plan")
            self.assertEqual(payload["schema_version"], "0.1")
            self.assertEqual(payload["mode"], "dry-run")
            self.assertEqual(payload["continuation_decision"]["decision"], "needs-proofcheck")
            self.assertFalse(payload["blocked"])
            self.assertEqual(len(payload["would_run"]), 1)
            step = payload["would_run"][0]
            self.assertEqual(step["phase"], "proofcheck")
            self.assertEqual(step["command"], [
                "orro",
                "proofcheck",
                str(run_dir),
                "--home",
                str(home),
                "--out",
                str(run_dir / "proofcheck-verdict.json"),
            ])
            self.assertEqual(step["engine"], "Depone")
            self.assertFalse(step["executes_workers"])
            self.assertTrue(step["verifies_evidence"])
            self.assertFalse(payload["boundary"]["executes_commands"])
            self.assertFalse(payload["boundary"]["verifies_evidence"])
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())

    def test_orro_auto_dry_run_after_proofcheck_plans_handoff_without_writing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            next_code, next_payload = self._orro_next(run_dir, home)
            self.assertEqual(next_code, 0)
            self.assertEqual(next_payload["decision"], "ready-for-handoff")

            code, payload = self._orro_auto_dry_run(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(payload["continuation_decision"]["decision"], "ready-for-handoff")
            self.assertEqual(payload["would_run"], [
                {
                    "phase": "handoff",
                    "command": [
                        "orro",
                        "handoff",
                        str(run_dir),
                        "--home",
                        str(home),
                        "--out",
                        str(run_dir / "orro-handoff.json"),
                    ],
                    "engine": "ORRO/witnessd",
                    "executes_workers": False,
                    "verifies_evidence": False,
                    "requires_human": False,
                }
            ])
            self.assertFalse((run_dir / "orro-handoff.json").exists())

    def test_orro_auto_dry_run_after_handoff_is_complete_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            self._handoff_out(run_dir)

            code, payload = self._orro_auto_dry_run(run_dir, home)

            self.assertEqual(code, 0)
            self.assertIn(payload["decision"], {"complete", "noop"})
            self.assertEqual(payload["would_run"], [])
            self.assertFalse(payload["blocked"])

    def test_orro_auto_dry_run_blocks_without_suggesting_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            verdict_path = run_dir / "proofcheck-verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            verdict["decision"] = "fail"
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

            code, payload = self._orro_auto_dry_run(run_dir, home)

            self.assertEqual(code, 1)
            self.assertEqual(payload["decision"], "blocked")
            self.assertTrue(payload["blocked"])
            self.assertEqual(payload["would_run"], [])
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_AUTO_BLOCKED")

    def test_orro_auto_dry_run_invalid_and_scout_only_dirs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            missing = root / "missing-run"

            code, payload = self._orro_auto_dry_run(missing, home)
            self.assertEqual(code, 2)
            self.assertEqual(payload["continuation_decision"]["decision"], "invalid-run-dir")
            self.assertEqual(payload["would_run"], [])

            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect repo", "--repo", str(repo)]), 0)
            scout_dir = Path(json.loads(scout_stdout.getvalue())["context_pack"]).parent

            code, payload = self._orro_auto_dry_run(scout_dir, home)
            self.assertEqual(code, 1)
            self.assertEqual(payload["would_run"], [])
            commands = [step.get("phase") for step in payload["would_run"]]
            self.assertNotIn("handoff", commands)

    def test_orro_auto_requires_dry_run_and_out_writes_same_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            out = run_dir / "orro-auto-plan.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "auto", str(run_dir), "--home", str(home), "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_DRY_RUN_REQUIRED")

            code, payload = self._orro_auto_dry_run(run_dir, home, "--out", str(out))
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)

    def test_orro_auto_once_after_proofrun_runs_only_proofcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            existing_runs = set((home / "runs").iterdir())

            code, receipt = self._orro_auto_once(run_dir, home)

            self.assertEqual(code, 0)
            self.assertEqual(receipt["kind"], "orro-auto-receipt")
            self.assertEqual(receipt["schema_version"], "0.1")
            self.assertEqual(receipt["mode"], "once")
            self.assertTrue(receipt["executed"])
            self.assertEqual(receipt["decision_before"], "needs-proofcheck")
            self.assertEqual(receipt["executed_phase"], "proofcheck")
            self.assertEqual(receipt["command"], [
                "orro",
                "proofcheck",
                str(run_dir),
                "--home",
                str(home),
                "--out",
                str(run_dir / "proofcheck-verdict.json"),
            ])
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["decision_after"], "ready-for-handoff")
            self.assertIn("proofcheck-verdict.json", receipt["wrote"])
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertFalse((run_dir / "orro-handoff.json").exists())
            self.assertEqual(set((home / "runs").iterdir()), existing_runs)
            self.assertFalse(receipt["boundary"]["launches_workers"])
            self.assertFalse(receipt["boundary"]["executes_proofrun"])
            self.assertFalse(receipt["boundary"]["verifies_evidence_itself"])
            self.assertTrue(receipt["boundary"]["delegates_verification_to_depone"])
            self.assertFalse(receipt["boundary"]["raises_assurance"])

    def test_orro_auto_once_after_proofcheck_runs_only_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)

            code, receipt = self._orro_auto_once(run_dir, home)

            self.assertEqual(code, 0)
            self.assertTrue(receipt["executed"])
            self.assertEqual(receipt["decision_before"], "ready-for-handoff")
            self.assertEqual(receipt["executed_phase"], "handoff")
            self.assertEqual(receipt["command"], [
                "orro",
                "handoff",
                str(run_dir),
                "--home",
                str(home),
                "--out",
                str(run_dir / "orro-handoff.json"),
            ])
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["decision_after"], "complete")
            self.assertIn("orro-handoff.json", receipt["wrote"])
            self.assertTrue((run_dir / "orro-handoff.json").is_file())
            self.assertFalse(receipt["boundary"]["approves_merge"])
            self.assertFalse(receipt["boundary"]["raises_assurance"])

    def test_orro_auto_once_after_complete_noops_without_writing_default_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            handoff = run_dir / "orro-handoff.json"
            self._handoff_out(run_dir)
            before = handoff.read_text(encoding="utf-8")

            code, receipt = self._orro_auto_once(run_dir, home)

            self.assertEqual(code, 0)
            self.assertFalse(receipt["executed"])
            self.assertEqual(receipt["decision_before"], "complete")
            self.assertEqual(receipt["decision_after"], "complete")
            self.assertEqual(receipt["executed_phase"], None)
            self.assertEqual(receipt["wrote"], [])
            self.assertEqual(handoff.read_text(encoding="utf-8"), before)
            self.assertFalse((run_dir / "orro-auto-receipt.json").exists())

    def test_orro_auto_once_blocks_without_executing_for_blocked_scout_and_invalid_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            missing = root / "missing-run"

            missing_code, missing_receipt = self._orro_auto_once(missing, home)
            self.assertEqual(missing_code, 2)
            self.assertFalse(missing_receipt["executed"])
            self.assertEqual(missing_receipt["decision_before"], "invalid-run-dir")
            self.assertEqual(missing_receipt["command"], [])

            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect repo", "--repo", str(repo)]), 0)
            scout_dir = Path(json.loads(scout_stdout.getvalue())["context_pack"]).parent

            scout_code, scout_receipt = self._orro_auto_once(scout_dir, home)
            self.assertEqual(scout_code, 1)
            self.assertFalse(scout_receipt["executed"])
            self.assertEqual(scout_receipt["command"], [])
            self.assertFalse((scout_dir / "proofcheck-verdict.json").exists())
            self.assertFalse((scout_dir / "orro-handoff.json").exists())

    def test_orro_auto_once_mode_errors_and_out_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            out = run_dir / "orro-auto-receipt.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "auto", str(run_dir), "--home", str(home), "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_DRY_RUN_REQUIRED")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "auto",
                        "--dry-run",
                        "--once",
                        str(run_dir),
                        "--home",
                        str(home),
                        "--json",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_MODE_CONFLICT")

            code, receipt = self._orro_auto_once(run_dir, home, "--out", str(out))
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), receipt)

    def test_orro_auto_once_module_and_witnessd_orro_alias_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)

            orro_auto = self._orro_module_run(
                ["auto", "--once", str(run_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(orro_auto.returncode, 0, orro_auto.stderr)
            payload = json.loads(orro_auto.stdout)
            self.assertEqual(payload["kind"], "orro-auto-receipt")
            self.assertEqual(payload["executed_phase"], "proofcheck")
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir_2, _payload = self._proofrun(root)
            witnessd_orro_auto = self._module_run(
                ["orro", "auto", "--once", str(run_dir_2), "--home", str(home), "--json"]
            )
            self.assertEqual(witnessd_orro_auto.returncode, 0, witnessd_orro_auto.stderr)
            payload_2 = json.loads(witnessd_orro_auto.stdout)
            self.assertEqual(payload_2["kind"], "orro-auto-receipt")
            self.assertEqual(payload_2["executed_phase"], "proofcheck")
            self.assertTrue((run_dir_2 / "proofcheck-verdict.json").is_file())

    def test_orro_auto_until_complete_after_proofrun_runs_proofcheck_then_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            existing_runs = set((home / "runs").iterdir())

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "2")

            self.assertEqual(code, 0)
            self.assertEqual(session["kind"], "orro-auto-session")
            self.assertEqual(session["schema_version"], "0.1")
            self.assertEqual(session["mode"], "until-complete")
            self.assertEqual(session["max_steps"], 2)
            self.assertEqual(session["steps_executed"], 2)
            self.assertEqual(session["decision_initial"], "needs-proofcheck")
            self.assertEqual(session["decision_final"], "complete")
            self.assertTrue(session["complete"])
            self.assertFalse(session["blocked"])
            self.assertEqual([step["executed_phase"] for step in session["steps"]], ["proofcheck", "handoff"])
            self.assertEqual(session["steps"][0]["decision_after"], "ready-for-handoff")
            self.assertEqual(session["steps"][1]["decision_after"], "complete")
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertTrue((run_dir / "orro-handoff.json").is_file())
            self.assertEqual(set((home / "runs").iterdir()), existing_runs)
            self.assertFalse(session["boundary"]["launches_workers"])
            self.assertFalse(session["boundary"]["executes_proofrun"])
            self.assertFalse(session["boundary"]["verifies_evidence_itself"])
            self.assertFalse(session["boundary"]["raises_assurance"])

    def test_orro_auto_until_complete_max_steps_one_stops_after_proofcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "1")

            self.assertEqual(code, 1)
            self.assertEqual(session["kind"], "orro-auto-session")
            self.assertEqual(session["steps_executed"], 1)
            self.assertEqual(session["decision_initial"], "needs-proofcheck")
            self.assertEqual(session["decision_final"], "ready-for-handoff")
            self.assertFalse(session["complete"])
            self.assertTrue(session["blocked"])
            self.assertEqual(session["error"]["code"], "ERR_ORRO_AUTO_MAX_STEPS_REACHED")
            self.assertEqual([step["executed_phase"] for step in session["steps"]], ["proofcheck"])
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertFalse((run_dir / "orro-handoff.json").exists())

    def test_orro_auto_until_complete_after_proofcheck_runs_handoff_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "2")

            self.assertEqual(code, 0)
            self.assertEqual(session["decision_initial"], "ready-for-handoff")
            self.assertEqual(session["decision_final"], "complete")
            self.assertEqual(session["steps_executed"], 1)
            self.assertEqual([step["executed_phase"] for step in session["steps"]], ["handoff"])
            self.assertTrue((run_dir / "orro-handoff.json").is_file())

    def test_orro_auto_until_complete_after_complete_noops_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            self._handoff_out(run_dir)
            proofcheck_before = (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            handoff_before = (run_dir / "orro-handoff.json").read_text(encoding="utf-8")

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "2")

            self.assertEqual(code, 0)
            self.assertEqual(session["decision_initial"], "complete")
            self.assertEqual(session["decision_final"], "complete")
            self.assertEqual(session["steps_executed"], 0)
            self.assertTrue(session["complete"])
            self.assertEqual(session["steps"], [])
            self.assertEqual((run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8"), proofcheck_before)
            self.assertEqual((run_dir / "orro-handoff.json").read_text(encoding="utf-8"), handoff_before)
            self.assertFalse((run_dir / "orro-auto-session.json").exists())

    def test_orro_auto_until_complete_blocks_without_proofrun_for_blocked_scout_and_invalid_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            missing = root / "missing-run"

            missing_code, missing_session = self._orro_auto_until_complete(missing, home, "--max-steps", "2")
            self.assertEqual(missing_code, 2)
            self.assertEqual(missing_session["decision_initial"], "invalid-run-dir")
            self.assertEqual(missing_session["steps"], [])

            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect repo", "--repo", str(repo)]), 0)
            scout_dir = Path(json.loads(scout_stdout.getvalue())["context_pack"]).parent

            scout_code, scout_session = self._orro_auto_until_complete(scout_dir, home, "--max-steps", "2")
            self.assertEqual(scout_code, 1)
            self.assertFalse(scout_session["complete"])
            self.assertTrue(scout_session["blocked"])
            self.assertEqual(scout_session["steps"], [])
            self.assertFalse((scout_dir / "proofcheck-verdict.json").exists())
            self.assertFalse((scout_dir / "orro-handoff.json").exists())

    def test_orro_auto_until_complete_blocked_verdict_does_not_run_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck_out(home, run_dir)
            verdict_path = run_dir / "proofcheck-verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            verdict["decision"] = "fail"
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "2")

            self.assertEqual(code, 1)
            self.assertEqual(session["decision_initial"], "blocked")
            self.assertEqual(session["decision_final"], "blocked")
            self.assertFalse(session["complete"])
            self.assertTrue(session["blocked"])
            self.assertEqual(session["steps"], [])
            self.assertFalse((run_dir / "orro-handoff.json").exists())

    def test_orro_auto_until_complete_mode_max_steps_and_out_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            out = run_dir / "orro-auto-session.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "auto", "--until-complete", str(run_dir), "--home", str(home), "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_MAX_STEPS_REQUIRED")

            for max_steps in ("0", "3"):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(
                        [
                            "orro",
                            "auto",
                            "--until-complete",
                            str(run_dir),
                            "--home",
                            str(home),
                            "--max-steps",
                            max_steps,
                            "--json",
                        ]
                    )
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_MAX_STEPS_INVALID")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "auto",
                        "--dry-run",
                        "--until-complete",
                        str(run_dir),
                        "--home",
                        str(home),
                        "--max-steps",
                        "2",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_MODE_CONFLICT")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "auto",
                        "--once",
                        "--until-complete",
                        str(run_dir),
                        "--home",
                        str(home),
                        "--max-steps",
                        "2",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "ERR_ORRO_AUTO_MODE_CONFLICT")

            code, session = self._orro_auto_until_complete(run_dir, home, "--max-steps", "2", "--out", str(out))
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), session)

    def test_orro_auto_until_complete_module_and_witnessd_orro_alias_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)

            orro_auto = self._orro_module_run(
                ["auto", "--until-complete", str(run_dir), "--home", str(home), "--max-steps", "2", "--json"]
            )

            self.assertEqual(orro_auto.returncode, 0, orro_auto.stderr)
            payload = json.loads(orro_auto.stdout)
            self.assertEqual(payload["kind"], "orro-auto-session")
            self.assertEqual(payload["decision_final"], "complete")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir_2, _payload = self._proofrun(root)
            witnessd_orro_auto = self._module_run(
                ["orro", "auto", "--until-complete", str(run_dir_2), "--home", str(home), "--max-steps", "2", "--json"]
            )
            self.assertEqual(witnessd_orro_auto.returncode, 0, witnessd_orro_auto.stderr)
            payload_2 = json.loads(witnessd_orro_auto.stdout)
            self.assertEqual(payload_2["kind"], "orro-auto-session")
            self.assertEqual(payload_2["decision_final"], "complete")

    def test_orro_next_module_and_witnessd_orro_alias_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)

            orro_next = self._orro_module_run(["next", str(run_dir), "--home", str(home), "--json"])
            witnessd_orro_next = self._module_run(
                ["orro", "next", str(run_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(orro_next.returncode, 0, orro_next.stderr)
            self.assertEqual(witnessd_orro_next.returncode, 0, witnessd_orro_next.stderr)
            self.assertEqual(json.loads(orro_next.stdout), json.loads(witnessd_orro_next.stdout))
            self.assertEqual(json.loads(orro_next.stdout)["decision"], "needs-proofcheck")

    def test_orro_auto_module_and_witnessd_orro_alias_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)

            orro_auto = self._orro_module_run(
                ["auto", "--dry-run", str(run_dir), "--home", str(home), "--json"]
            )
            witnessd_orro_auto = self._module_run(
                ["orro", "auto", "--dry-run", str(run_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(orro_auto.returncode, 0, orro_auto.stderr)
            self.assertEqual(witnessd_orro_auto.returncode, 0, witnessd_orro_auto.stderr)
            self.assertEqual(json.loads(orro_auto.stdout), json.loads(witnessd_orro_auto.stdout))
            payload = json.loads(orro_auto.stdout)
            self.assertEqual(payload["kind"], "orro-auto-plan")
            self.assertEqual(payload["continuation_decision"]["decision"], "needs-proofcheck")
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())

    def test_proofrun_role_lane_plan_forbidden_profiles_fail_before_run_dir(self) -> None:
        for profile in ("critic-only", "review-only", "verification-only"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo, home = self._init_home(root)
                plan_path = self._flowplan_out(root, "write two proof files", profile=profile)
                role_lane_path = self._role_lane_plan_out(root, "write two proof files", profile=profile)

                stdout = io.StringIO()
                with redirect_stdout(stdout):
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
                            "--role-lane-plan",
                            str(role_lane_path),
                            "--json",
                        ]
                    )

                self.assertEqual(code, 2)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(
                    payload["error"]["code"],
                    "ERR_ORRO_ROLE_LANE_PLAN_EXECUTION_FORBIDDEN",
                )
                self.assertFalse((home / "runs").exists())

    def test_proofrun_role_lane_plan_mismatch_or_malformed_fails_before_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = self._flowplan_out(root, "write two proof files", profile="code-change")
            mismatched = self._role_lane_plan_out(root, "different goal", profile="code-change")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
                        "--role-lane-plan",
                        str(mismatched),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_ROLE_LANE_PLAN_HASH_MISMATCH")
            self.assertFalse((home / "runs").exists())

            malformed = root / "malformed-role-lane-plan.json"
            malformed.write_text(json.dumps({"kind": "not-a-role-lane-plan"}), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
                        "--role-lane-plan",
                        str(malformed),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_ROLE_LANE_PLAN_INVALID")
            self.assertFalse((home / "runs").exists())

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

    def test_proofrun_workflow_plan_phase_forbidden_fails_before_execution(self) -> None:
        for profile in ("critic-only", "review-only", "verification-only"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo, home = self._init_home(root)
                plan_path = self._flowplan_out(root, "write two proof files", profile=profile)

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
                            "--json",
                        ]
                    )

                self.assertEqual(code, 2)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["error"]["code"], "ERR_ORRO_WORKFLOW_PLAN_PHASE_FORBIDDEN")
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

    def test_orro_module_flowplan_accepts_rolepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            out = root / "role-lane-plan.json"

            flowplan = self._orro_module_run(
                [
                    "flowplan",
                    "plan goal",
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(out),
                    "--rolepack",
                    "developer",
                    "--model-policy",
                    "default",
                ]
            )

            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)
            lane = json.loads(out.read_text(encoding="utf-8"))["lanes"][0]
            self.assertEqual(lane["role_capability"]["role_id"], "runner")
            self.assertEqual(lane["model_source"], "model-policy")

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
            "next",
            "report",
            "auto",
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
        self.assertIn("deprecated", help_result.stderr)
        self.assertIn("ORRO package", help_result.stderr)
        self.assertIn("ORRO Flow", help_result.stdout)
        self.assertIn("init", help_result.stdout)
        self.assertIn("report", help_result.stdout)
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
            role_lane_path = self._role_lane_plan_out(root, "write two proof files")
            home, run_dir, _payload = self._proofrun(
                root,
                workflow_plan=plan_path,
                role_lane_plan=role_lane_path,
            )
            out = run_dir / "proofcheck-verdict.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertIn("workflow_plan", payload)
            self.assertIn("role_lane_plan", payload)
            self.assertEqual(payload["workflow_plan"]["path"], str(run_dir / "workflow-plan.json"))
            self.assertEqual(payload["role_lane_plan"]["path"], str(run_dir / "role-lane-plan.json"))
            verdict_payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(verdict_payload["workflow_plan"], payload["workflow_plan"])
            self.assertEqual(verdict_payload["role_lane_plan"], payload["role_lane_plan"])
            self.assertFalse(verdict_payload["workflow_plan"]["boundary"]["raises_assurance"])
            self.assertFalse(verdict_payload["role_lane_plan"]["boundary"]["raises_assurance"])

    def test_proofcheck_preserves_role_dispatch_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            home, run_dir, proofrun_payload = self._proofrun(root, workflow_plan=plan_path)
            out = run_dir / "proofcheck-verdict.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["workflow_role_dispatch"]["sha256"],
                proofrun_payload["workflow_role_dispatch"]["sha256"],
            )
            verdict_payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                verdict_payload["workflow_role_dispatch"]["sha256"],
                proofrun_payload["workflow_role_dispatch"]["sha256"],
            )

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

    def test_proofcheck_non_pass_verdict_persists_trust_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            out = evidence_dir / "proofcheck-verdict.json"

            def fake_depone(
                _command: list[str], *, env: dict[str, str]
            ) -> tuple[int, dict]:
                verdict = {
                    "decision": "fail",
                    "verifier_command": "proofcheck",
                }
                out.write_text(json.dumps(verdict) + "\n", encoding="utf-8")
                return 1, {**verdict, "out": str(out)}

            with patch("witnessd.__main__._run_depone_json", side_effect=fake_depone):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(
                        ["proofcheck", str(evidence_dir), "--out", str(out)]
                    )

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["trust_anchor"], "self-signed")
            verdict = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(verdict["trust_anchor"], "self-signed")
            self.assertFalse(verdict["independent_trust_anchor"])
            self.assertNotIn("orro_binding", verdict)

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

    def test_proofrun_help_labels_direct_shell_capture_as_capture_only(self) -> None:
        result = self._module_run(["proofrun", "--help"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("capture-only", result.stdout)
        self.assertIn("not proofcheckable", result.stdout)
        self.assertIn("orro scout", result.stdout)

    def test_direct_shell_capture_stays_blocked_with_workflow_contract_guidance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            evidence_dir = root / "direct-capture"
            evidence_dir.mkdir()

            proofrun = self._module_run(
                [
                    "orro",
                    "proofrun",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    str(repo),
                    "--out",
                    str(evidence_dir / "lane.json"),
                    "--log",
                    str(evidence_dir / "run.log"),
                    "--task-id",
                    "direct-capture",
                    "--",
                    "printf ok",
                ]
            )
            self.assertEqual(proofrun.returncode, 0, proofrun.stderr)

            proofcheck = self._module_run(
                ["orro", "proofcheck", str(evidence_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(proofcheck.returncode, 1, proofcheck.stdout)
            payload = json.loads(proofcheck.stdout)
            self.assertEqual(payload["decision"], "blocked")
            missing = {
                error["message"]
                for error in payload["errors"]
                if error["code"] == "ERR_ORRO_ARTIFACT_REQUIRED_MISSING"
            }
            for artifact in (
                "repo-profile.json",
                "context-pack.json",
                "skillpack-lock.json",
                "verification-recipe.json",
                "verification-receipt.json",
                "pr-handoff.json",
            ):
                self.assertIn(f"required artifact is missing: {artifact}", missing)
            self.assertTrue(payload["workflow_contract"]["capture_only"])
            self.assertIn("orro scout", payload["message"])
            self.assertIn("flowplan", payload["message"])

    def test_documented_scout_flowplan_proofrun_proofcheck_sequence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = root / "workflow-plan.json"

            scout = self._module_run(
                [
                    "orro",
                    "scout",
                    "write proof file",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                ]
            )
            self.assertEqual(scout.returncode, 0, scout.stderr)
            scout_dir = Path(json.loads(scout.stdout)["run_dir"])

            flowplan = self._module_run(
                [
                    "orro",
                    "flowplan",
                    "write proof file",
                    "--root",
                    str(repo),
                    "--profile",
                    "code-change",
                    "--out",
                    str(plan_path),
                ]
            )
            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)

            proofrun = self._module_run(
                [
                    "orro",
                    "proofrun",
                    "write proof file",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                    "--workflow-plan",
                    str(plan_path),
                    "--run-dir",
                    str(scout_dir),
                    "--allow-reference-adapter",
                    "--json",
                ]
            )
            self.assertEqual(proofrun.returncode, 0, proofrun.stderr)

            proofcheck = self._module_run(
                [
                    "orro",
                    "proofcheck",
                    str(scout_dir),
                    "--home",
                    str(home),
                    "--out",
                    str(scout_dir / "proofcheck-verdict.json"),
                    "--json",
                ]
            )
            self.assertEqual(proofcheck.returncode, 0, proofcheck.stderr)
            self.assertEqual(json.loads(proofcheck.stdout)["decision"], "pass")

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
            self.assertNotIn("advisory_provenance", payload)
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

    def test_proofcheck_surfaces_advisory_provenance_as_separate_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._emit_sketch_bundle(root, home, run_dir)
            decision_path = run_dir / "orro-sketch.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["decision_record"]["decision"] = "tampered"
            decision_path.write_text(
                json.dumps(decision, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            proofcheck = self._proofcheck_out(home, run_dir)

            self.assertEqual(proofcheck["decision"], "pass")
            self.assertIn("advisory_provenance", proofcheck)
            self.assertEqual(
                proofcheck["advisory_provenance"]["decision"], "REFUTE"
            )
            self.assertFalse(
                proofcheck["advisory_provenance"]["boundary"][
                    "can_change_evidence_verdict"
                ]
            )
            written = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(written["decision"], "pass")
            self.assertEqual(
                written["advisory_provenance"]["decision"], "REFUTE"
            )

    def test_handoff_rederives_advisory_provenance_and_refutes_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._emit_sketch_bundle(root, home, run_dir)
            self._proofcheck_out(home, run_dir)

            handoff = self._handoff_out(run_dir)

            self.assertIn("advisory_provenance", handoff)
            self.assertEqual(handoff["advisory_provenance"]["decision"], "PASS")
            advisory_ref = next(
                ref
                for ref in handoff["decision_refs"]
                if ref.get("track") == "advisory-provenance"
            )
            self.assertEqual(advisory_ref["decision"], "PASS")
            self.assertEqual(
                advisory_ref["path"], "advisory-provenance-bundle.json"
            )

            (run_dir / "orro-handoff.json").unlink()
            decision_path = run_dir / "orro-sketch.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["decision_record"]["decision"] = "tampered"
            decision_path.write_text(
                json.dumps(decision, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            out = run_dir / "orro-handoff.json"
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "handoff",
                        str(run_dir),
                        "--out",
                        str(out),
                        "--json",
                    ]
                )

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            error = json.loads(stdout.getvalue())
            self.assertEqual(
                error["error"]["code"],
                "ERR_ORRO_HANDOFF_ADVISORY_PROVENANCE_REFUTED",
            )
            self.assertEqual(error["advisory_provenance"]["decision"], "REFUTE")

    def test_handoff_includes_workflow_plan_binding_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            role_lane_path = self._role_lane_plan_out(root, "write two proof files")
            home, run_dir, _payload = self._proofrun(
                root,
                workflow_plan=plan_path,
                role_lane_plan=role_lane_path,
            )
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
            self.assertIn("role_lane_plan", payload)
            self.assertEqual(payload["workflow_plan"]["path"], str(run_dir / "workflow-plan.json"))
            self.assertEqual(payload["role_lane_plan"]["path"], str(run_dir / "role-lane-plan.json"))
            self.assertFalse(payload["workflow_plan"]["boundary"]["raises_assurance"])
            self.assertFalse(payload["role_lane_plan"]["boundary"]["raises_assurance"])
            written = json.loads((run_dir / "orro-handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(written["workflow_plan"], payload["workflow_plan"])
            self.assertEqual(written["role_lane_plan"], payload["role_lane_plan"])

    def test_handoff_includes_role_dispatch_reference_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = self._flowplan_out(root, "write two proof files")
            home, run_dir, proofrun_payload = self._proofrun(root, workflow_plan=plan_path)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "orro",
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
            self.assertEqual(
                payload["workflow_role_dispatch"]["sha256"],
                proofrun_payload["workflow_role_dispatch"]["sha256"],
            )
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])

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
            out = home / "orro-engine-lock.json"
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
            out = home / "orro-engine-lock.json"
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
                    "--allow-reference-adapter",
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
