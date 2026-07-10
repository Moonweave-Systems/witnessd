"""Direct coverage for witnessd's own team worktree preparation.

This behavior used to be tested through Depone's `team_worktree_prep` module.
Depone's Phase 4 extraction (`Extract deprecated execution surfaces to
witnessd`) turned that module into a delegating shim with no logic of its own,
so the functional coverage moved here to test the canonical implementation
directly. See depone/docs/phase2-tcb-extraction.md.

The `depone team-worktree-prep` CLI itself (arg parsing, dispatch, receipt
file writing) remains Depone's to test against a mocked `run`; this file
covers what actually creates/selects worktrees.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.team_worktree_prep import (
    TEAM_LAUNCH_PREFLIGHT_KIND,
    TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION,
    build_team_worktree_prep,
    validate_team_worktree_prep,
)


class TeamWorktreePrepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _init_repo(self) -> tuple[Path, str]:
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "r@x.invalid"], cwd=repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "prep-test"], cwd=repo, check=True
        )
        (repo / "sample.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        return repo, base

    def _preflight(
        self, base_commit: str, planned_worktree: str = "lane-1"
    ) -> dict[str, object]:
        return {
            "kind": TEAM_LAUNCH_PREFLIGHT_KIND,
            "schema_version": TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION,
            "decision": "pass",
            "launch_intent": "plan-only",
            "base_commit": base_commit,
            "lane_count": 1,
            "lanes": [
                {
                    "lane_id": "lane-1",
                    "planned_worktree": planned_worktree,
                    "evidence_dir": "lane-1",
                    "worktree_receipt": "lane-1/worktree-receipt.json",
                }
            ],
            "boundary": {
                "launches_agents": False,
                "creates_worktrees": False,
                "executes_commands": False,
                "mutates_worktree": False,
                "calls_live_models": False,
                "raises_assurance": False,
            },
            "errors": [],
        }

    def test_missing_worktree_blocks_without_create_flag(self) -> None:
        repo, base = self._init_repo()
        worktree_root = self.root / "worktrees"

        receipt = build_team_worktree_prep(
            self._preflight(base),
            repo_root=repo,
            worktree_root=worktree_root,
            create_worktree=False,
        )

        self.assertEqual(receipt["decision"], "blocked")
        self.assertFalse((worktree_root / "lane-1").exists())
        self.assertIn(
            "ERR_TEAM_WORKTREE_PREP_CREATE_FLAG_REQUIRED",
            {error["code"] for error in receipt["errors"]},
        )
        self.assertEqual(validate_team_worktree_prep(receipt), [])

    def test_create_worktree_flag_records_created_lane_without_launching_agents(
        self,
    ) -> None:
        repo, base = self._init_repo()
        worktree_root = self.root / "worktrees"

        receipt = build_team_worktree_prep(
            self._preflight(base),
            repo_root=repo,
            worktree_root=worktree_root,
            create_worktree=True,
        )

        lane = receipt["lanes"][0]
        created_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_root / "lane-1",
            text=True,
        ).strip()
        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(lane["action"], "created")
        self.assertEqual(lane["head_commit"], base)
        self.assertEqual(created_head, base)
        self.assertEqual(receipt["boundary"]["runs_git_worktree_add"], True)
        self.assertEqual(receipt["boundary"]["launches_agents"], False)
        self.assertEqual(receipt["boundary"]["executes_lane_commands"], False)
        self.assertEqual(receipt["deprecation"]["migration_target"], "witnessd")
        self.assertEqual(validate_team_worktree_prep(receipt), [])

    def test_existing_worktree_is_selected_without_create_flag(self) -> None:
        repo, base = self._init_repo()
        worktree_root = self.root / "worktrees"
        worktree_root.mkdir()
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_root / "lane-1"), base],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

        receipt = build_team_worktree_prep(
            self._preflight(base),
            repo_root=repo,
            worktree_root=worktree_root,
            create_worktree=False,
        )

        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["lanes"][0]["action"], "selected")
        self.assertEqual(receipt["boundary"]["runs_git_worktree_add"], False)

    def test_path_traversal_planned_worktree_blocks(self) -> None:
        repo, base = self._init_repo()

        receipt = build_team_worktree_prep(
            self._preflight(base, "../outside"),
            repo_root=repo,
            worktree_root=self.root / "worktrees",
            create_worktree=True,
        )

        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn(
            "ERR_TEAM_WORKTREE_PREP_PATH_INVALID",
            {error["code"] for error in receipt["errors"]},
        )


if __name__ == "__main__":
    unittest.main()
