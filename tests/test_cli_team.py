import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main

_HAS_OPENSSL = shutil.which("openssl") is not None


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w3"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _fake_codex(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "mkdir -p pkg\n"
        "echo adapter > pkg/adapter.py\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_records_home(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p pkg\n"
        "if [ -f \"$CODEX_HOME/auth.json\" ]; then auth_status=present; else auth_status=missing; fi\n"
        "printf '%s\\n%s\\n' \"$CODEX_HOME\" \"$auth_status\" > pkg/codex-home.txt\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_records_home_from_prompt(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "target=$(cat)\n"
        "if [ -z \"$target\" ]; then target=pkg/codex-home.txt; fi\n"
        "mkdir -p \"$(dirname \"$target\")\"\n"
        "if [ -f \"$CODEX_HOME/auth.json\" ]; then auth_status=present; else auth_status=missing; fi\n"
        "printf '%s\\n%s\\n' \"$CODEX_HOME\" \"$auth_status\" > \"$target\"\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


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
        "mkdir -p pkg\n"
        "cat > pkg/prompt.txt\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


@unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
class TestTeamCli(unittest.TestCase):
    def test_team_run_emits_ledger_and_pending_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            repo.mkdir()
            _seed_repo(repo)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "team",
                        "run",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--lane",
                        "lane-a:pkg/a.py",
                        "--lane",
                        "lane-b:pkg/b.py",
                    ]
                )

            self.assertEqual(code, 0)
            text = stdout.getvalue()
            self.assertIn("evidence-pending", text)
            self.assertNotIn("VERIFIED", text)
            self.assertTrue((out_dir / "team-ledger.json").exists())
            self.assertTrue((out_dir / "lane-a" / "capture-manifest.json").exists())
            self.assertTrue((out_dir / "lane-b" / "worktree-lane-receipt.json").exists())


    def test_team_run_accepts_adapter_lane_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            bindir = root / "bin"
            repo.mkdir()
            bindir.mkdir()
            _seed_repo(repo)
            _fake_codex(bindir)
            old_path = os.environ.get("PATH", "")
            stdout = io.StringIO()

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stdout(stdout):
                    code = main(
                        [
                            "team",
                            "run",
                            "--repo",
                            str(repo),
                            "--out",
                            str(out_dir),
                            "--lane",
                            "shell-lane:pkg/shell.py",
                            "--lane",
                            "adapter-lane:adapter=codex:tier=quick:region=pkg/adapter.py:prompt=write adapter",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            ledger = json.loads((out_dir / "team-ledger.json").read_text())
            kinds = {lane["lane_id"]: lane["runner_adapter_kind"] for lane in ledger["lanes"]}
            self.assertEqual(kinds, {"shell-lane": "shell", "adapter-lane": "codex"})
            self.assertTrue((out_dir / "adapter-lane" / "runner-receipt.json").exists())

    def test_team_ledger_json_reports_pending_depone_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            repo.mkdir()
            _seed_repo(repo)
            self.assertEqual(
                main(
                    [
                        "team",
                        "run",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--lane",
                        "lane-a:pkg/a.py",
                    ]
                ),
                0,
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "team-ledger",
                        "--ledger",
                        str(out_dir / "team-ledger.json"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            status = json.loads(stdout.getvalue())
            self.assertEqual(status["decision"], "evidence-pending")
            self.assertEqual(status["pending"], 1)
            self.assertIn("pending Depone verification", status["message"])

    def test_team_run_claim_conflict_excludes_second_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            repo.mkdir()
            _seed_repo(repo)

            code = main(
                [
                    "team",
                    "run",
                    "--repo",
                    str(repo),
                    "--out",
                    str(out_dir),
                    "--lane",
                    "lane-a:pkg/shared.py",
                    "--lane",
                    "lane-b:pkg/shared.py",
                ]
            )

            self.assertEqual(code, 0)
            ledger = json.loads((out_dir / "team-ledger.json").read_text())
            self.assertEqual([lane["lane_id"] for lane in ledger["lanes"]], ["lane-a"])
            runlog = (out_dir / "runlog.jsonl").read_text()
            self.assertIn("claim-conflict", runlog)

    def test_team_run_seeds_codex_auth_only_into_isolated_state_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = root / "state"
            bindir = root / "bin"
            auth_source = root / "auth.json"
            repo.mkdir()
            bindir.mkdir()
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _seed_repo(repo)
            _fake_codex_records_home(bindir)
            old_path = os.environ.get("PATH", "")
            stdout = io.StringIO()

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stdout(stdout):
                    code = main(
                        [
                            "team",
                            "run",
                            "--repo",
                            str(repo),
                            "--out",
                            str(out_dir),
                            "--state-root",
                            str(state_root),
                            "--codex-auth-source",
                            str(auth_source),
                            "--lane",
                            "adapter-lane:adapter=codex:tier=quick:region=pkg/codex-home.txt:prompt=write adapter",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            isolated_auth = state_root / ".witnessd" / "codex-home" / "auth.json"
            self.assertEqual(
                isolated_auth.read_text(encoding="utf-8"),
                auth_source.read_text(encoding="utf-8"),
            )
            self.assertEqual(oct(isolated_auth.stat().st_mode & 0o777), "0o600")
            worktree_file = next((out_dir / "worktrees").glob("adapter-lane*/pkg/codex-home.txt"))
            lines = worktree_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [os.path.realpath(lines[0]), lines[1]],
                [os.path.realpath(state_root / ".witnessd" / "codex-home"), "present"],
            )
            evidence_bytes = b"".join(
                path.read_bytes() for path in out_dir.rglob("*") if path.is_file()
            )
            self.assertNotIn(b"subscription", evidence_bytes)

    def test_team_run_rejects_state_root_inside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = out_dir / "state"
            bindir = root / "bin"
            auth_source = root / "auth.json"
            repo.mkdir()
            bindir.mkdir()
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _seed_repo(repo)
            _fake_codex_records_home(bindir)
            old_path = os.environ.get("PATH", "")
            stderr = io.StringIO()

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stderr(stderr):
                    code = main(
                        [
                            "team",
                            "run",
                            "--repo",
                            str(repo),
                            "--out",
                            str(out_dir),
                            "--state-root",
                            str(state_root),
                            "--codex-auth-source",
                            str(auth_source),
                            "--lane",
                            "adapter-lane:adapter=codex:tier=quick:region=pkg/codex-home.txt:prompt=write adapter",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 2)
            self.assertIn("ERR_TEAM_RUN_STATE_ROOT_INSIDE_OUTPUT", stderr.getvalue())
            self.assertFalse((state_root / ".witnessd" / "codex-home" / "auth.json").exists())

    def test_team_run_isolates_multiple_codex_lanes_under_state_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = root / "state"
            bindir = root / "bin"
            auth_source = root / "auth.json"
            repo.mkdir()
            bindir.mkdir()
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _seed_repo(repo)
            _fake_codex_records_home_from_prompt(bindir)
            old_path = os.environ.get("PATH", "")

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                code = main(
                    [
                        "team",
                        "run",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--state-root",
                        str(state_root),
                        "--codex-auth-source",
                        str(auth_source),
                        "--lane",
                        "alpha:adapter=codex:tier=quick:region=pkg/alpha-home.txt:prompt=pkg/alpha-home.txt",
                        "--lane",
                        "beta:adapter=codex:tier=quick:region=pkg/beta-home.txt:prompt=pkg/beta-home.txt",
                    ]
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0)
            alpha_file = next((out_dir / "worktrees").glob("alpha*/pkg/alpha-home.txt"))
            beta_file = next((out_dir / "worktrees").glob("beta*/pkg/beta-home.txt"))
            alpha_lines = alpha_file.read_text(encoding="utf-8").splitlines()
            beta_lines = beta_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(alpha_lines[1], "present")
            self.assertEqual(beta_lines[1], "present")
            self.assertNotEqual(alpha_lines[0], beta_lines[0])
            self.assertTrue(
                os.path.realpath(alpha_lines[0]).startswith(os.path.realpath(state_root))
            )
            self.assertTrue(
                os.path.realpath(beta_lines[0]).startswith(os.path.realpath(state_root))
            )
            self.assertEqual(
                (Path(alpha_lines[0]) / "auth.json").read_text(encoding="utf-8"),
                auth_source.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (Path(beta_lines[0]) / "auth.json").read_text(encoding="utf-8"),
                auth_source.read_text(encoding="utf-8"),
            )
            self.assertFalse((state_root / ".witnessd" / "codex-home" / "auth.json").exists())

    def test_team_run_rejects_multiple_codex_lanes_without_state_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            bindir = root / "bin"
            repo.mkdir()
            bindir.mkdir()
            _seed_repo(repo)
            _fake_codex_records_home_from_prompt(bindir)
            old_path = os.environ.get("PATH", "")
            stderr = io.StringIO()

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                with redirect_stderr(stderr):
                    code = main(
                        [
                            "team",
                            "run",
                            "--repo",
                            str(repo),
                            "--out",
                            str(out_dir),
                            "--lane",
                            "alpha:adapter=codex:tier=quick:region=pkg/alpha-home.txt:prompt=pkg/alpha-home.txt",
                            "--lane",
                            "beta:adapter=codex:tier=quick:region=pkg/beta-home.txt:prompt=pkg/beta-home.txt",
                        ]
                    )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 2)
            self.assertIn("ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED", stderr.getvalue())
            self.assertFalse((repo / ".witnessd").exists())

    def test_team_run_lane_prompt_file_overrides_inline_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = root / "state"
            bindir = root / "bin"
            prompt_file = root / "prompt.txt"
            repo.mkdir()
            bindir.mkdir()
            prompt_text = "implement feature: preserve colon\nand newline\n"
            prompt_file.write_text(prompt_text, encoding="utf-8")
            _seed_repo(repo)
            _fake_codex_writes_prompt(bindir)
            old_path = os.environ.get("PATH", "")

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                code = main(
                    [
                        "team",
                        "run",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--state-root",
                        str(state_root),
                        "--lane-prompt-file",
                        f"impl={prompt_file}",
                        "--lane",
                        "impl:adapter=codex:tier=quick:region=pkg/prompt.txt:prompt=inline-prompt",
                    ]
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0)
            prompt_out = next((out_dir / "worktrees").glob("impl*/pkg/prompt.txt"))
            self.assertEqual(prompt_out.read_text(encoding="utf-8"), prompt_text)

    def test_team_run_keeps_inline_prompt_without_prompt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = root / "state"
            bindir = root / "bin"
            repo.mkdir()
            bindir.mkdir()
            _seed_repo(repo)
            _fake_codex_writes_prompt(bindir)
            old_path = os.environ.get("PATH", "")

            try:
                os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
                code = main(
                    [
                        "team",
                        "run",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--state-root",
                        str(state_root),
                        "--lane",
                        "impl:adapter=codex:tier=quick:region=pkg/prompt.txt:prompt=inline-prompt",
                    ]
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(code, 0)
            prompt_out = next((out_dir / "worktrees").glob("impl*/pkg/prompt.txt"))
            self.assertEqual(prompt_out.read_text(encoding="utf-8"), "inline-prompt")


if __name__ == "__main__":
    unittest.main()
