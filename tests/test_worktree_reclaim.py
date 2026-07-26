import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from witnessd.worktree import reclaim_run_worktrees


class RunWorktreeReclaimTests(unittest.TestCase):
    def _repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "test")):
            subprocess.run(["git", *args], cwd=repo, check=True)
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True
        ).stdout.strip()
        return repo, base

    def test_reclaims_only_run_worktrees_and_preserves_branch_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            run_dir = Path(tmp) / "run"
            worktrees = run_dir / "worktrees"
            worktree = worktrees / "lane"
            subprocess.run(["git", "worktree", "add", "-b", "witnessd/lane", str(worktree), base], cwd=repo, check=True)
            external = Path(tmp) / "external"
            subprocess.run(["git", "worktree", "add", "-b", "external", str(external), base], cwd=repo, check=True)

            result = reclaim_run_worktrees(repo=repo, run_dir=run_dir)

            self.assertEqual(result["action"], "reclaimed")
            self.assertFalse(worktree.exists())
            self.assertTrue(external.exists())
            self.assertEqual(
                subprocess.run(["git", "show-ref", "--verify", "refs/heads/witnessd/lane"], cwd=repo).returncode,
                0,
            )

    def test_keep_worktree_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            run_dir = Path(tmp) / "run"
            worktree = run_dir / "worktrees" / "lane"
            subprocess.run(["git", "worktree", "add", "-b", "witnessd/lane", str(worktree), base], cwd=repo, check=True)

            result = reclaim_run_worktrees(repo=repo, run_dir=run_dir, keep=True)

            self.assertEqual(result["action"], "kept")
            self.assertTrue(worktree.exists())
            self.assertEqual(result["reason"], "--keep-worktree")

    def test_reclaim_failure_is_reported_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            run_dir = Path(tmp) / "run"
            worktree = run_dir / "worktrees" / "lane"
            subprocess.run(["git", "worktree", "add", "-b", "witnessd/lane", str(worktree), base], cwd=repo, check=True)

            original = subprocess.run

            def failed_run(command, *args, **kwargs):
                if command[:3] == ["git", "worktree", "remove"]:
                    return subprocess.CompletedProcess(command, 1, "", "locked")
                return original(command, *args, **kwargs)

            with mock.patch("witnessd.worktree.subprocess.run", side_effect=failed_run):
                result = reclaim_run_worktrees(repo=repo, run_dir=run_dir)

            self.assertEqual(result["action"], "reclaim-failed")
            self.assertEqual(result["errors"], ["git worktree remove failed: locked"])
            self.assertTrue(worktree.exists())


if __name__ == "__main__":
    unittest.main()
