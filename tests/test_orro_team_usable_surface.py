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
from witnessd.role_capability import validate_rolepack


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _fake_codex_writes_prompt(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p orro\n"
        "cat > orro/task-output.txt\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_noops(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "cat >/dev/null\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


class OrroTeamUsableSurfaceTests(unittest.TestCase):
    def test_orro_team_init_scaffolds_valid_rolepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team_path = root / ".orro" / "team.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "team",
                        "init",
                        "--template",
                        "developer",
                        "--out",
                        str(team_path),
                        "--yes",
                    ]
                )

            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "created")
            self.assertFalse(payload["can_change_evidence_verdict"])
            rolepack = json.loads(team_path.read_text(encoding="utf-8"))
            validate_rolepack(rolepack)
            self.assertEqual(rolepack["kind"], "moonweave-rolepack")
            self.assertEqual(rolepack["schema_version"], "0.2")
            runner = next(grant for grant in rolepack["grants"] if grant["role_id"] == "runner")
            self.assertEqual(runner["tools"], {"mcp": [], "allow": []})

    def test_orro_team_init_refuses_overwrite_without_yes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            team_path = Path(tmp) / ".orro" / "team.json"
            team_path.parent.mkdir()
            team_path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "team",
                        "init",
                        "--template",
                        "developer",
                        "--out",
                        str(team_path),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("ERR_ORRO_TEAM_INIT_EXISTS", stderr.getvalue())

    def test_orro_team_init_interactive_requires_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "team",
                        "init",
                        "--interactive",
                        "--out",
                        str(Path(tmp) / ".orro" / "team.json"),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn(
                "ERR_ORRO_TEAM_INIT_INTERACTIVE_REQUIRES_TTY", stderr.getvalue()
            )

    def test_orro_team_go_threads_plan_run_proofcheck_report_and_uses_task_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "run"
            team_path = root / ".orro" / "team.json"
            bindir = root / "bin"
            repo.mkdir()
            bindir.mkdir()
            _seed_repo(repo)
            _fake_codex_writes_prompt(bindir)

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["init", "--home", str(home), "--depone-root", str(_depone_root())]),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "orro",
                            "team",
                            "init",
                            "--out",
                            str(team_path),
                            "--role",
                            "runner:codex:gpt-5.5",
                            "--write-scope",
                            "orro/task-output.txt",
                            "--yes",
                        ]
                    ),
                    0,
                )

            old_path = os.environ.get("PATH", "")
            stdout = io.StringIO()
            stderr = io.StringIO()
            task = "Create orro/task-output.txt with the exact line: usable surface"
            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(
                        [
                            "orro",
                            "team",
                            "go",
                            "placeholder goal",
                            "--task",
                            task,
                            "--repo",
                            str(repo),
                            "--home",
                            str(home),
                            "--team",
                            str(team_path),
                            "--run-dir",
                            str(run_dir),
                            "--json",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0, f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["proofcheck"]["decision"], "pass")
            self.assertTrue((run_dir / "workflow-plan.json").is_file())
            self.assertTrue((run_dir / "role-lane-plan.json").is_file())
            self.assertTrue((run_dir / "proofcheck-verdict.json").is_file())
            self.assertTrue((run_dir / "orro-report.json").is_file())
            role_lane_plan = json.loads((run_dir / "role-lane-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(role_lane_plan["lanes"][0]["prompt"], task)
            prompt_out = next((run_dir / "worktrees").glob("runner*/orro/task-output.txt"))
            self.assertEqual(prompt_out.read_text(encoding="utf-8"), task)

    def test_orro_team_go_reports_no_work_without_fake_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            run_dir = root / "run"
            team_path = root / ".orro" / "team.json"
            bindir = root / "bin"
            repo.mkdir()
            bindir.mkdir()
            _seed_repo(repo)
            _fake_codex_noops(bindir)

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["init", "--home", str(home), "--depone-root", str(_depone_root())]),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "orro",
                            "team",
                            "init",
                            "--out",
                            str(team_path),
                            "--role",
                            "runner:codex:gpt-5.5",
                            "--write-scope",
                            "orro/noop.txt",
                            "--yes",
                        ]
                    ),
                    0,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            old_path = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(
                        [
                            "orro",
                            "team",
                            "go",
                            "do nothing",
                            "--task",
                            "Do not modify any files.",
                            "--repo",
                            str(repo),
                            "--home",
                            str(home),
                            "--team",
                            str(team_path),
                            "--run-dir",
                            str(run_dir),
                            "--json",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertNotEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "blocked")
            self.assertTrue(payload["no_work_detected"])
            self.assertIn("did not touch files", payload["message"])


if __name__ == "__main__":
    unittest.main()
