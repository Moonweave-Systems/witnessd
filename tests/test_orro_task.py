from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.cli.status import apply_tidy, build_status, build_tidy_inventory
from witnessd.orro_roadmap import ERR_ORRO_ROADMAP_ITEM_UNKNOWN, write_roadmap
from witnessd.orro_task import ERR_ORRO_TASK_INVALID, begin_task, read_task_descriptor, scan_task_worktrees


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
            command = "/bin/sh -c 'printf stdout-tail; printf stderr-tail >&2; test \"$1\" = item-one && test \"$2\" = orro/item-one' sh {item_id} {branch}"
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
            self.assertEqual(receipt["stdout_tail"], "stdout-tail")
            self.assertEqual(receipt["stderr_tail"], "stderr-tail")

    def test_resume_skips_hook_unless_explicitly_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            command = "/bin/sh -c 'printf x >> {path}/hook.log'"
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": command}):
                first = begin_task(repo=repo, item_id="item-one", base="HEAD")
                second = begin_task(repo=repo, item_id="item-one", base="HEAD")
                third = begin_task(repo=repo, item_id="item-one", base="HEAD", open=True)
            self.assertEqual(first["state"], "created")
            self.assertEqual(second["state"], "resumed")
            self.assertEqual(second["message"], "open hook skipped on resume (pass --open to re-open)")
            self.assertEqual(third["state"], "resumed")
            self.assertEqual((repo / ".orro" / "worktrees" / "item-one" / "hook.log").read_text(), "xx")

    def test_attached_task_skips_hook_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            _git(repo, "branch", "orro/item-one", "HEAD").check_returncode()
            command = "/bin/sh -c 'printf x >> {path}/hook.log'"
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": command}):
                payload = begin_task(repo=repo, item_id="item-one", base="HEAD")
            self.assertEqual(payload["state"], "attached")
            self.assertEqual(payload["message"], "open hook skipped on resume (pass --open to re-open)")
            self.assertFalse((repo / ".orro" / "worktrees" / "item-one" / "hook.log").exists())

    def test_open_and_no_open_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            with self.assertRaisesRegex(ValueError, "--open and --no-open") as caught:
                begin_task(repo=repo, item_id="item-one", base="HEAD", open=True, no_open=True)
            self.assertEqual(caught.exception.code, ERR_ORRO_TASK_INVALID)

    def test_binary_hook_output_is_recovered_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            command = "/usr/bin/python3 -c 'import sys; sys.stdout.buffer.write(bytes([255])); sys.stderr.buffer.write(bytes([254]))'"
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": command}):
                begin_task(repo=repo, item_id="item-one", base="HEAD")
            receipt = json.loads((repo / ".orro" / "worktrees" / "item-one" / "task-open-receipt.json").read_text())
            self.assertEqual(receipt["stdout_tail"], "�")
            self.assertEqual(receipt["stderr_tail"], "�")

    def test_human_output_shows_rendered_command_and_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _seed_repo(repo)
            _roadmap(repo)
            command = "/bin/echo WORKSPACE-42 {item_id} {path}"
            output = io.StringIO()
            with patch.dict(os.environ, {"ORRO_TASK_OPEN_COMMAND": command}), redirect_stdout(output):
                code = main(["orro-task", "begin", "item-one", "--repo", str(repo)])
            rendered = command.replace("{item_id}", "item-one").replace("{path}", str(repo / ".orro" / "worktrees" / "item-one"))
            self.assertEqual(code, 0)
            self.assertIn(f"open hook command: {rendered}", output.getvalue())
            self.assertIn("open hook may open/focus an external workspace; this is a workspace-runtime action, not a Codex thread and not proof/evidence", output.getvalue())

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
