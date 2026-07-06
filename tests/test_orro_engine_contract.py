from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO engine contract fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: orro-engine-contract-fixture\n---\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


class OrroEngineContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def _json_command(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(args)
        return code, json.loads(stdout.getvalue())

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

    def _flowplan(self, root: Path, goal: str, *, role_lanes: bool = False) -> Path:
        out = root / ("role-lane-plan.json" if role_lanes else "workflow-plan.json")
        args = [
            "orro",
            "flowplan",
            goal,
            "--root",
            str(root),
            "--profile",
            "code-change",
        ]
        if role_lanes:
            args.extend(["--role-lanes-out", str(out)])
        else:
            args.extend(["--out", str(out)])
        code, _payload = self._json_command(args)
        self.assertEqual(code, 0)
        self.assertTrue(out.is_file())
        return out

    def _proofrun(self, root: Path, *, with_contract_artifacts: bool = False) -> tuple[Path, Path, dict]:
        repo, home = self._init_home(root)
        args = [
            "orro",
            "proofrun",
            "write ORRO engine contract fixture",
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--max-parallel",
            "1",
        ]
        if with_contract_artifacts:
            workflow_plan = self._flowplan(root, "write ORRO engine contract fixture")
            role_lane_plan = self._flowplan(
                root,
                "write ORRO engine contract fixture",
                role_lanes=True,
            )
            args.extend(["--workflow-plan", str(workflow_plan), "--role-lane-plan", str(role_lane_plan)])
        code, payload = self._json_command(args)
        self.assertEqual(code, 0, payload)
        return home, Path(payload["run_dir"]), payload

    def _proofcheck(self, home: Path, run_dir: Path) -> tuple[int, dict]:
        return self._json_command(
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

    def test_contract_doc_lists_artifacts_and_forbids_third_engine(self) -> None:
        contract = (self.ROOT / "docs" / "orro-engine-contract-v0.md").read_text(
            encoding="utf-8"
        )
        conformance = (self.ROOT / "docs" / "orro-conformance" / "README.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Depone verifies; witnessd executes; ORRO exposes the workflow.", contract)
        self.assertIn("Depone remains", contract)
        self.assertIn("verifier-authoritative", contract)
        self.assertIn("Become a third engine", contract)
        self.assertIn("docs/orro-conformance/manifest.json", conformance)
        for artifact in [
            "repo-profile.json",
            "context-pack.json",
            "sealed-plan.json",
            "workflow-plan.json",
            "workflow-plan-binding.json",
            "role-lane-plan.json",
            "role-lane-plan-binding.json",
            "workflow-role-dispatch.json",
            "team-ledger.json",
            "team-ledger-verdict.json",
            "verification-recipe.json",
            "verification-receipt.json",
            "proofcheck-verdict.json",
            "orro-continuation-decision.json",
            "orro-auto-plan.json",
            "orro-auto-receipt.json",
            "orro-auto-session.json",
            "orro-report.json",
            "orro-handoff.json",
            "orro-engine-lock.json",
        ]:
            self.assertIn(artifact, contract)

    def test_contract_checker_passes(self) -> None:
        result = subprocess.run(
            ["python3", "scripts/check_orro_engine_contract.py"],
            cwd=self.ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_orro_engine_contract: pass", result.stdout)

    def test_proofrun_outputs_contract_artifacts_and_proofcheck_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, payload = self._proofrun(Path(tmp), with_contract_artifacts=True)

            for filename in [
                "workflow-plan.json",
                "workflow-plan-binding.json",
                "role-lane-plan.json",
                "role-lane-plan-binding.json",
                "workflow-role-dispatch.json",
                "team-ledger.json",
                "team-ledger-verdict.json",
            ]:
                self.assertTrue((run_dir / filename).is_file(), filename)
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())
            self.assertNotIn("final_trust", payload)

            code, proofcheck_payload = self._proofcheck(home, run_dir)

            self.assertEqual(code, 0, proofcheck_payload)
            self.assertEqual(proofcheck_payload["verifier_command"], "team-ledger")
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())

    def test_handoff_requires_bound_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))

            code, missing_payload = self._json_command(
                ["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json"), "--json"]
            )
            self.assertNotEqual(code, 0)
            self.assertEqual(missing_payload["error"]["code"], "ERR_ORRO_HANDOFF_PROOFCHECK_REQUIRED")

            self._proofcheck(home, run_dir)
            code, handoff_payload = self._json_command(
                ["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json"), "--json"]
            )

            self.assertEqual(code, 0, handoff_payload)
            self.assertTrue((run_dir / "orro-handoff.json").is_file())
            self.assertFalse(handoff_payload["boundary"]["approves_merge"])
            self.assertFalse(handoff_payload["boundary"]["raises_assurance"])

    def test_report_and_auto_respect_gates_without_running_proofrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            before_runs = set((home / "runs").iterdir())

            report_code, report_payload = self._json_command(
                ["orro", "report", str(run_dir), "--home", str(home), "--json"]
            )
            self.assertEqual(report_code, 0)
            self.assertEqual(report_payload["summary"]["state"], "needs-proofcheck")
            self.assertFalse(report_payload["summary"]["complete"])

            dry_code, dry_payload = self._json_command(
                ["orro", "auto", "--dry-run", str(run_dir), "--home", str(home), "--json"]
            )
            once_code, once_payload = self._json_command(
                ["orro", "auto", "--once", str(run_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(dry_code, 0)
            self.assertEqual(dry_payload["would_run"][0]["phase"], "proofcheck")
            self.assertEqual(once_code, 0, once_payload)
            self.assertEqual(once_payload["executed_phase"], "proofcheck")
            self.assertFalse(once_payload["boundary"]["executes_proofrun"])
            self.assertFalse(once_payload["boundary"]["launches_workers"])
            self.assertEqual(set((home / "runs").iterdir()), before_runs)
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertFalse((run_dir / "orro-handoff.json").exists())

    def test_wrapper_artifacts_alone_do_not_claim_ready_or_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo, home = self._init_home(root)
            wrapper_dir = root / "wrapper-only"
            wrapper_dir.mkdir()
            for filename, payload in {
                "workflow-plan.json": {"kind": "orro-workflow-plan", "schema_version": "0.1"},
                "role-lane-plan.json": {"kind": "orro-role-lane-plan", "schema_version": "0.1"},
                "workflow-role-dispatch.json": {
                    "kind": "orro-role-dispatch",
                    "schema_version": "0.1",
                    "roles": [{"role_id": "runner", "status": "executed"}],
                },
                "orro-auto-session.json": {
                    "kind": "orro-auto-session",
                    "schema_version": "0.1",
                    "complete": True,
                },
                "orro-report.json": {
                    "kind": "orro-report",
                    "schema_version": "0.1",
                    "summary": {"state": "complete"},
                },
            }.items():
                (wrapper_dir / filename).write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

            next_code, next_payload = self._json_command(
                ["orro", "next", str(wrapper_dir), "--home", str(home), "--json"]
            )
            report_code, report_payload = self._json_command(
                ["orro", "report", str(wrapper_dir), "--home", str(home), "--json"]
            )
            auto_code, auto_payload = self._json_command(
                ["orro", "auto", "--dry-run", str(wrapper_dir), "--home", str(home), "--json"]
            )

            self.assertEqual(next_code, 1)
            self.assertIn(next_payload["decision"], {"blocked", "evidence-pending"})
            self.assertNotIn("handoff", " ".join(next_payload["next_allowed"]))
            self.assertEqual(report_code, 1)
            self.assertFalse(report_payload["summary"]["complete"])
            self.assertFalse(report_payload["handoff"]["ready_for_handoff"])
            self.assertEqual(auto_code, 1)
            self.assertEqual(auto_payload["would_run"], [])


if __name__ == "__main__":
    unittest.main()
