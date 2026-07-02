import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.worktree import create_lane_worktree


def _seed_repo(root: str) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "w3"], cwd=root, check=True)
    (Path(root) / "seed.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class TestWorktreeCreate(unittest.TestCase):
    def test_creates_worktree_at_base_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = os.path.join(directory, "repo")
            os.mkdir(repo)
            base = _seed_repo(repo)

            worktree = create_lane_worktree(
                repo_root=repo,
                lane_id="lane-a",
                base_commit=base,
                worktrees_dir=os.path.join(directory, "worktrees"),
            )

            self.assertTrue(os.path.isdir(worktree))
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head, base)

    def test_invalid_base_commit_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = os.path.join(directory, "repo")
            os.mkdir(repo)
            _seed_repo(repo)

            with self.assertRaisesRegex(Exception, "ERR_WORKTREE_CREATE_FAILED"):
                create_lane_worktree(
                    repo_root=repo,
                    lane_id="lane-a",
                    base_commit="not-a-commit",
                    worktrees_dir=os.path.join(directory, "worktrees"),
                )


if __name__ == "__main__":
    unittest.main()
