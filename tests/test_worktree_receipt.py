import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.worktree_receipt import (
    WORKTREE_LANE_RECEIPT_KIND,
    WORKTREE_LANE_RECEIPT_SCHEMA_VERSION,
)

from witnessd.worktree import WorktreeError, build_worktree_lane_receipt


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w3"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class TestWorktreeReceipt(unittest.TestCase):
    def test_shape_and_clean_dirty(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            base = _seed_repo(repo)
            (repo / "seed.txt").write_text("b\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "change", "-q"], cwd=repo, check=True)

            receipt = build_worktree_lane_receipt(
                worktree=str(repo),
                base_commit=base,
                evidence_dir="lane-a",
                commands=[{"command": "python3 -m unittest", "exit_code": 0}],
            )

            self.assertEqual(receipt["kind"], WORKTREE_LANE_RECEIPT_KIND)
            self.assertEqual(
                receipt["schema_version"], WORKTREE_LANE_RECEIPT_SCHEMA_VERSION
            )
            self.assertIs(receipt["dirty"], False)
            self.assertEqual(receipt["dirty_files"], [])
            self.assertEqual(receipt["changed_files"], ["seed.txt"])
            self.assertEqual(receipt["evidence_dir"], "lane-a")
            self.assertIs(receipt["boundary"]["git_read_only"], True)
            self.assertIs(receipt["boundary"]["executes_commands"], False)

    def test_dirty_files_are_reported_without_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            base = _seed_repo(repo)
            (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

            receipt = build_worktree_lane_receipt(
                worktree=str(repo),
                base_commit=base,
                evidence_dir="lane-a",
            )

            self.assertIs(receipt["dirty"], True)
            self.assertEqual(receipt["dirty_files"], ["scratch.txt"])

    def test_invalid_evidence_dir_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            base = _seed_repo(repo)

            with self.assertRaisesRegex(WorktreeError, "ERR_WORKTREE_RECEIPT_PATH_INVALID"):
                build_worktree_lane_receipt(
                    worktree=str(repo),
                    base_commit=base,
                    evidence_dir="../outside",
                )


if __name__ == "__main__":
    unittest.main()
