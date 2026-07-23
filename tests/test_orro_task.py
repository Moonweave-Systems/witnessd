from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.cli.status import apply_tidy, build_status, build_tidy_inventory
from witnessd.orro_roadmap import ERR_ORRO_ROADMAP_ITEM_UNKNOWN, write_roadmap
from witnessd.orro_task import begin_task, read_task_descriptor, scan_task_worktrees


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )


def _seed_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main").check_returncode()
    _git(repo, "config", "user.email", "task@example.invalid").check_returncode()
    _git(repo, "config", "user.name", "ORRO Task").check_returncode()
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md").check_returncode()
    _git(repo, "commit", "-qm", "seed").check_returncode()


def _roadmap(repo: Path, *, status: str | None = None) -> None:
    item = {"id": "item-one", "title": "Item one"}
    if status is not None:
        item["status"] = status
    write_roadmap(
        repo,
        {"kind": "orro-roadmap", "schema_version": "0.1", "items": [item]},
    )


class OrroTaskTests(unittest.TestCase):
    def test_unknown_item_reuses_roadmap_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            with self.assertRaises(Exception) as caught:
                begin_task(repo=repo, item_id="missing", base="HEAD", no_open=True)
            self.assertEqual(caught.exception.code, ERR_ORRO_ROADMAP_ITEM_UNKNOWN)

    def test_begin_seals_descriptor_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)

            first = begin_task(repo=repo, item_id="item-one", base="HEAD", no_open=True)
            second = begin_task(repo=repo, item_id="item-one", base="HEAD", no_open=True)
            worktree = repo / ".orro" / "worktrees" / "item-one"
            descriptor = read_task_descriptor(worktree)

            self.assertEqual(first["state"], "created")
            self.assertEqual(second["state"], "resumed")
            self.assertEqual(descriptor["kind"], "orro-task-descriptor")
            self.assertEqual(descriptor["schema_version"], "0.1")
            self.assertEqual(descriptor["item_id"], "item-one")
            self.assertEqual(descriptor["worktree"], str(worktree))
            self.assertEqual(descriptor["branch"], "orro/item-one")
            self.assertTrue(descriptor["base_commit"])
            self.assertEqual(scan_task_worktrees(repo)["item-one"]["descriptor"], descriptor)

    def test_open_hook_substitutes_values_and_records_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            command = "/bin/sh -c 'test \"$1\" = item-one && test \"$2\" = orro/item-one' sh {item_id} {branch}"
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": command}):
                payload = begin_task(repo=repo, item_id="item-one", base="HEAD")
            receipt = json.loads(
                (repo / ".orro" / "worktrees" / "item-one" / "task-open-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["open_hook_exit_code"], 0)
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["command"], command.replace("{item_id}", "item-one").replace("{branch}", "orro/item-one").replace("{path}", str(repo / ".orro" / "worktrees" / "item-one")))

    def test_open_hook_failure_keeps_worktree_and_surfaces_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": "/bin/sh -c 'exit 7'"}):
                payload = begin_task(repo=repo, item_id="item-one", base="HEAD")
            self.assertEqual(payload["open_hook_exit_code"], 7)
            self.assertTrue((repo / ".orro" / "worktrees" / "item-one").is_dir())
            receipt = json.loads(
                (repo / ".orro" / "worktrees" / "item-one" / "task-open-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(receipt["exit_code"], 7)

    def test_status_and_tidy_use_live_task_state_and_verified_item_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            begin_task(repo=repo, item_id="item-one", base="HEAD", no_open=True)
            worktree = repo / ".orro" / "worktrees" / "item-one"
            (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            status = build_status(repo=repo, home=repo / ".witnessd")
            self.assertEqual(status["items"][0]["workspace"], ".orro/worktrees/item-one (branch orro/item-one, dirty)")
            inventory = build_tidy_inventory(repo=repo, home=repo / ".witnessd")
            self.assertEqual(inventory["task_worktrees"][0]["kind"], "task")
            dirty_result = apply_tidy(repo=repo, inventory=inventory)
            self.assertEqual(dirty_result["actions"][0]["reason"], "dirty")
            self.assertTrue(worktree.exists())

            (worktree / "dirty.txt").unlink()
            inventory = build_tidy_inventory(repo=repo, home=repo / ".witnessd")
            with patch(
                "witnessd.cli.status.build_status",
                return_value={"items": [{"id": "item-one", "status": "marked-done (unverified)"}]},
            ):
                result = apply_tidy(repo=repo, inventory=inventory)
            self.assertEqual(result["actions"][0]["reason"], "item status marked-done (unverified)")
            self.assertTrue(worktree.exists())

            with patch(
                "witnessd.cli.status.build_status",
                return_value={"items": [{"id": "item-one", "status": "done (verified)"}]},
            ):
                inventory = build_tidy_inventory(repo=repo, home=repo / ".witnessd")
                result = apply_tidy(repo=repo, inventory=inventory)
            self.assertEqual(result["actions"][0]["action"], "removed")
            self.assertFalse(worktree.exists())
            self.assertTrue(_git(repo, "branch", "--list", "orro/item-one").stdout.strip())

    def test_mismatched_descriptor_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            begin_task(repo=repo, item_id="item-one", base="HEAD", no_open=True)
            path = repo / ".orro" / "worktrees" / "item-one" / ".orro-task.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["branch"] = "wrong"
            path.write_text(json.dumps(payload), encoding="utf-8")
            status = build_status(repo=repo, home=repo / ".witnessd")
            self.assertEqual(status["items"][0]["workspace"], "unverified descriptor")


if __name__ == "__main__":
    unittest.main()
