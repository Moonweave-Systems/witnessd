from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.orro_team_surface import apply_task_prompt_to_role_lane_plan


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO runtime hardening fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: orro-runtime-hardening-fixture\n---\n", encoding="utf-8")
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


class OrroRuntimeHardeningTests(unittest.TestCase):
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

    def _flowplan_out(self, root: Path, goal: str, *, role_lanes: bool = False) -> Path:
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
            rolepack = _write_shell_rolepack(root)
            args.extend(["--role-lanes-out", str(out)])
            args.extend(["--rolepack-file", str(rolepack)])
        else:
            args.extend(["--out", str(out)])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(args)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(out.is_file())
        if role_lanes:
            payload = json.loads(out.read_text(encoding="utf-8"))
            patched = apply_task_prompt_to_role_lane_plan(
                payload,
                task=f"Perform the declared {goal} task",
            )["role_lane_plan"]
            out.write_text(json.dumps(patched), encoding="utf-8")
        return out

    def _proofrun(self, root: Path, *, with_workflow: bool = False) -> tuple[Path, Path, dict]:
        repo, home = self._init_home(root)
        args = [
            "orro",
            "proofrun",
            "write runtime hardening fixture",
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--max-parallel",
            "1",
        ]
        if with_workflow:
            workflow_plan = self._flowplan_out(root, "write runtime hardening fixture")
            role_lane_plan = self._flowplan_out(
                root,
                "write runtime hardening fixture",
                role_lanes=True,
            )
            args.extend(["--workflow-plan", str(workflow_plan), "--role-lane-plan", str(role_lane_plan)])
        args.append("--allow-reference-adapter")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(args)
        self.assertEqual(code, 0, stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        return home, Path(payload["run_dir"]), payload

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

    def _json_command(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(args)
        return code, json.loads(stdout.getvalue())

    def _command_code(self, args: list[str]) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(args)

    def _next(self, run_dir: Path, home: Path) -> tuple[int, dict]:
        from witnessd.orro_next import decide_next

        return decide_next(run_dir, home=home)

    def _report(self, run_dir: Path, home: Path) -> tuple[int, dict]:
        return self._json_command(["orro", "status", str(run_dir), "--home", str(home), "--json"])

    def _auto_dry_run(self, run_dir: Path, home: Path) -> tuple[int, dict]:
        return self._json_command(["orro", "auto", "--dry-run", str(run_dir), "--home", str(home), "--json"])

    def _auto_once(self, run_dir: Path, home: Path) -> tuple[int, dict]:
        return self._json_command(["orro", "auto", "--once", str(run_dir), "--home", str(home), "--json"])

    def _auto_until_complete(self, run_dir: Path, home: Path, *, max_steps: int = 2) -> tuple[int, dict]:
        return self._json_command(
            [
                "orro",
                "auto",
                "--until-complete",
                str(run_dir),
                "--home",
                str(home),
                "--max-steps",
                str(max_steps),
                "--json",
            ]
        )

    def test_malformed_team_ledger_blocks_next_report_handoff_and_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            existing_runs = set((home / "runs").iterdir())
            (run_dir / "team-ledger.json").write_text("not-json", encoding="utf-8")

            next_code, next_payload = self._next(run_dir, home)
            self.assertEqual(next_code, 1)
            self.assertEqual(next_payload["decision"], "blocked")
            self.assertTrue(next_payload["blocked"])
            self.assertNotIn("proofcheck", " ".join(next_payload["next_allowed"]))

            report_code, report_payload = self._report(run_dir, home)
            self.assertEqual(report_code, 1)
            self.assertEqual(report_payload["summary"]["state"], "blocked")
            self.assertFalse(report_payload["summary"]["complete"])
            self.assertFalse(report_payload["handoff"]["ready_for_handoff"])

            dry_code, dry_payload = self._auto_dry_run(run_dir, home)
            self.assertEqual(dry_code, 1)
            self.assertEqual(dry_payload["would_run"], [])

            once_code, once_payload = self._auto_once(run_dir, home)
            self.assertEqual(once_code, 1)
            self.assertFalse(once_payload["executed"])
            self.assertEqual(once_payload["command"], [])

            until_code, until_payload = self._auto_until_complete(run_dir, home)
            self.assertEqual(until_code, 1)
            self.assertEqual(until_payload["steps"], [])
            self.assertFalse(until_payload["complete"])

            handoff_code = self._command_code(
                ["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json")]
            )
            self.assertNotEqual(handoff_code, 0)
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())
            self.assertFalse((run_dir / "orro-handoff.json").exists())
            self.assertEqual(set((home / "runs").iterdir()), existing_runs)

    def test_corrupted_workflow_bindings_and_dispatch_block_continuation(self) -> None:
        corrupted_files = [
            "workflow-plan-binding.json",
            "role-lane-plan-binding.json",
            "workflow-role-dispatch.json",
        ]
        for filename in corrupted_files:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                home, run_dir, _payload = self._proofrun(Path(tmp), with_workflow=True)
                (run_dir / filename).write_text("not-json", encoding="utf-8")

                next_code, next_payload = self._next(run_dir, home)
                self.assertEqual(next_code, 1)
                self.assertEqual(next_payload["decision"], "blocked")
                self.assertTrue(next_payload["blocked"])
                self.assertEqual(next_payload["next_allowed"], [])

                dry_code, dry_payload = self._auto_dry_run(run_dir, home)
                self.assertEqual(dry_code, 1)
                self.assertEqual(dry_payload["would_run"], [])
                self.assertFalse((run_dir / "proofcheck-verdict.json").exists())
                self.assertFalse((run_dir / "orro-handoff.json").exists())

    def test_non_executing_surfaces_do_not_create_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            before_runs = set((home / "runs").iterdir())

            report_out = run_dir / "orro-report.json"
            auto_out = run_dir / "orro-auto-plan.json"
            commands = [
                ["orro", "advise", "fix parser typo", "--repo", str(root), "--json"],
                ["orro", "status", str(run_dir), "--home", str(home), "--out", str(report_out), "--json"],
                ["orro", "auto", "--dry-run", str(run_dir), "--home", str(home), "--out", str(auto_out), "--json"],
            ]

            for args in commands:
                with self.subTest(command=args[:3]):
                    code, payload = self._json_command(args)
                    self.assertEqual(code, 0, payload)

            self.assertEqual(set((home / "runs").iterdir()), before_runs)
            self.assertFalse((run_dir / "proofcheck-verdict.json").exists())
            self.assertFalse((run_dir / "orro-handoff.json").exists())
            self.assertTrue(report_out.is_file())
            self.assertTrue(auto_out.is_file())

    def test_stale_handoff_stops_being_complete_if_proofcheck_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir, _payload = self._proofrun(root)
            self._proofcheck(home, run_dir)
            self._handoff(run_dir)

            verdict_path = run_dir / "proofcheck-verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            verdict["decision"] = "refuted"
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

            next_code, next_payload = self._next(run_dir, home)
            self.assertEqual(next_code, 1)
            self.assertEqual(next_payload["decision"], "blocked")

            report_code, report_payload = self._report(run_dir, home)
            self.assertEqual(report_code, 1)
            self.assertEqual(report_payload["summary"]["state"], "blocked")
            self.assertFalse(report_payload["summary"]["complete"])
            self.assertFalse(report_payload["handoff"]["ready_for_handoff"])

            dry_code, dry_payload = self._auto_dry_run(run_dir, home)
            self.assertEqual(dry_code, 1)
            self.assertEqual(dry_payload["would_run"], [])


if __name__ == "__main__":
    unittest.main()
