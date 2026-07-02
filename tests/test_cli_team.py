import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
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


if __name__ == "__main__":
    unittest.main()
