from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.cli.status import apply_tidy, build_tidy_inventory, render_tidy_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _seed_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tidy@example.invalid")
    _git(repo, "config", "user.name", "ORRO Tidy")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "seed")


def _add_worktree(repo: Path, path: Path, branch: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")


class OrroTidyTests(unittest.TestCase):
    def test_dry_run_inventory_uses_live_dirty_check_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            home = root / "home"
            run_dir = home / "runs" / "run-complete"
            clean = run_dir / "worktrees" / "clean"
            dirty = run_dir / "worktrees" / "dirty"
            _add_worktree(repo, clean, "tidy-clean")
            _add_worktree(repo, dirty, "tidy-dirty")
            (dirty / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            with patch(
                "witnessd.cli.status.decide_next",
                return_value=(0, {"decision": "complete"}),
            ):
                payload = build_tidy_inventory(repo=repo, home=home)

            by_name = {Path(item["path"]).name: item for item in payload["worktrees"]}
            self.assertFalse(by_name["clean"]["dirty"])
            self.assertTrue(by_name["dirty"]["dirty"])
            self.assertEqual(by_name["clean"]["run_state"], "complete")
            self.assertEqual(by_name["clean"]["branch"], "tidy-clean")
            self.assertTrue(by_name["clean"]["base_commit"])
            self.assertTrue(by_name["clean"]["head_commit"])
            self.assertTrue(clean.is_dir())
            self.assertTrue(dirty.is_dir())
            self.assertIn(str(repo), [item["path"] for item in payload["registered_outside_runs"]])
            self.assertIn("dry-run", render_tidy_text(payload))

    def test_apply_removes_only_clean_complete_and_keeps_dirty_or_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            home = root / "home"
            complete_run = home / "runs" / "run-complete"
            active_run = home / "runs" / "run-active"
            clean_complete = complete_run / "worktrees" / "clean-complete"
            dirty_complete = complete_run / "worktrees" / "dirty-complete"
            clean_active = active_run / "worktrees" / "clean-active"
            _add_worktree(repo, clean_complete, "clean-complete")
            _add_worktree(repo, dirty_complete, "dirty-complete")
            _add_worktree(repo, clean_active, "clean-active")
            (dirty_complete / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            def fake_decide(run_dir: Path, *, home: Path) -> tuple[int, dict[str, str]]:
                state = "complete" if run_dir.name == "run-complete" else "needs-proofcheck"
                return 0, {"decision": state}

            with patch("witnessd.cli.status.decide_next", side_effect=fake_decide):
                inventory = build_tidy_inventory(repo=repo, home=home)
                result = apply_tidy(repo=repo, inventory=inventory)

            self.assertFalse(clean_complete.exists())
            self.assertTrue(dirty_complete.exists())
            self.assertTrue(clean_active.exists())
            self.assertTrue(complete_run.is_dir())
            self.assertTrue(active_run.is_dir())
            actions = {Path(item["path"]).name: item for item in result["actions"]}
            self.assertEqual(actions["clean-complete"]["action"], "removed")
            self.assertEqual(actions["dirty-complete"]["action"], "kept")
            self.assertEqual(actions["dirty-complete"]["reason"], "dirty")
            self.assertEqual(actions["clean-active"]["action"], "kept")
            self.assertIn("run state needs-proofcheck", actions["clean-active"]["reason"])
            text = render_tidy_text(result)
            self.assertIn("kept: dirty", text)
            self.assertIn("kept: run state needs-proofcheck", text)

    def test_apply_prunes_registered_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            missing = root / "outside-missing"
            _add_worktree(repo, missing, "outside-missing")
            shutil.rmtree(missing)

            with patch("witnessd.cli.status.decide_next"):
                inventory = build_tidy_inventory(repo=repo, home=root / "home")
                result = apply_tidy(repo=repo, inventory=inventory)

            missing_action = next(
                item for item in result["actions"] if item["path"] == str(missing)
            )
            self.assertEqual(missing_action["action"], "pruned")
            registered = _git(repo, "worktree", "list", "--porcelain")
            self.assertNotIn(str(missing), registered)


if __name__ == "__main__":
    unittest.main()
