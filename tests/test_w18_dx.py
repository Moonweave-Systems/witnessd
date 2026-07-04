import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w18@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w18"], cwd=repo, check=True)
    (repo / "README.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


class W18DxCliTests(unittest.TestCase):
    def _run_ergonomic_goal(self, root: Path) -> tuple[Path, Path]:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()
        _seed_repo(repo)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["init", "--home", str(home), "--depone-root", str(depone_root)]),
                0,
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "run",
                    "write two independent files",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                ]
            )
        self.assertEqual(code, 0, stderr.getvalue())
        return home, Path(json.loads(stdout.getvalue())["run_dir"])

    def test_run_goal_repo_syntax_after_goal_emits_parallel_team_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._run_ergonomic_goal(root)
            payload = json.loads((run_dir / "team-ledger-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["lane_count"], 2)
            self.assertTrue((run_dir / "sealed-plan.json").is_file())
            self.assertTrue((run_dir / "team-ledger.json").is_file())
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())

    def test_verify_run_dir_rederives_with_pinned_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._run_ergonomic_goal(Path(tmp))
            (run_dir / "team-ledger-verdict.json").unlink()
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["verify", str(run_dir), "--home", str(home)])

            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["team_ledger"], str(run_dir / "team-ledger.json"))
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())


if __name__ == "__main__":
    unittest.main()
