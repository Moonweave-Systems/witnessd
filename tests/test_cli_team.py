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

    def test_team_ledger_json_passes_through_depone_verdict(self):
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
            verdict = json.loads(stdout.getvalue())
            self.assertEqual(verdict["decision"], "pass")
            self.assertIs(verdict["boundary"]["raises_assurance"], False)

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
