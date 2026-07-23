from __future__ import annotations

import shutil
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.cli.status import apply_tidy, build_tidy_inventory, render_tidy_text
from witnessd.orro_roadmap import seal_roadmap_binding, write_roadmap


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


def _write_companion_verdict(run_dir: Path, *, decision: str) -> None:
    import hashlib

    run_dir.mkdir(parents=True, exist_ok=True)
    verdict = run_dir / "proofcheck-verdict.json"
    verdict.write_text(json.dumps({"decision": decision}) + "\n", encoding="utf-8")
    manifest = {
        "kind": "orro-companion-manifest",
        "verdict_ref": {
            "path": str(verdict),
            "sha256": hashlib.sha256(verdict.read_bytes()).hexdigest(),
        },
    }
    (run_dir / "companion-manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")


class OrroTidyTests(unittest.TestCase):
    def test_inventory_classifies_nested_run_and_never_removes_unknown_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            home = repo / ".witnessd"
            nested_run = repo / ".orro" / "worktrees" / "item-one" / ".witnessd" / "runs" / "run-nested"
            nested = nested_run / "worktrees" / "runner-one"
            external = root / "external"
            _add_worktree(repo, nested, "nested-runner")
            _add_worktree(repo, external, "external")
            _write_companion_verdict(nested_run, decision="pass")

            inventory = build_tidy_inventory(repo=repo, home=home)

            nested_record = next(item for item in inventory["worktrees"] if item["path"] == str(nested))
            self.assertEqual(nested_record["kind"], "nested-run")
            self.assertEqual(nested_record["run_state"], "companion-pass")
            outside_record = next(item for item in inventory["registered_outside_runs"] if item["path"] == str(external))
            self.assertEqual(outside_record["kind"], "unknown-external")

            result = apply_tidy(repo=repo, inventory=inventory)
            actions = {item["path"]: item for item in result["actions"]}
            self.assertEqual(actions[str(nested)]["action"], "removed")
            self.assertEqual(actions[str(external)]["reason"], "unknown external worktree")
            self.assertTrue(external.exists())

    def test_direct_run_is_distinct_from_nested_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            home = root / "home"
            direct = home / "runs" / "run-direct" / "worktrees" / "runner"
            _add_worktree(repo, direct, "direct-runner")
            with patch("witnessd.cli.status.decide_next", return_value=(0, {"decision": "complete"})):
                inventory = build_tidy_inventory(repo=repo, home=home)
            self.assertEqual(inventory["worktrees"][0]["kind"], "direct-run")

    def test_keep_checks_removes_only_oldest_unreferenced_check_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            write_roadmap(repo, {
                "kind": "orro-roadmap", "schema_version": "0.1",
                "items": [{"id": "evidence-item", "title": "Evidence item"}],
            })
            home = root / "home"
            runs = home / "runs"
            runs.mkdir(parents=True)
            check_dirs = [runs / f"check-{index:02d}" for index in range(4)]
            for index, run_dir in enumerate(check_dirs):
                run_dir.mkdir()
                verdict = run_dir / "proofcheck-verdict.json"
                verdict.write_text(json.dumps({"decision": "pass"}) + "\n", encoding="utf-8")
                manifest = {
                    "kind": "orro-companion-manifest",
                    "verdict_ref": {
                        "path": str(verdict),
                        "sha256": __import__("hashlib").sha256(verdict.read_bytes()).hexdigest(),
                    },
                }
                (run_dir / "companion-manifest.json").write_text(
                    json.dumps(manifest) + "\n", encoding="utf-8"
                )
                os.utime(run_dir, (index + 1, index + 1))
            seal_roadmap_binding(
                repo=repo, run_dir=check_dirs[0], item_id="evidence-item"
            )

            inventory = build_tidy_inventory(repo=repo, home=home)
            untouched = apply_tidy(repo=repo, inventory=inventory)
            self.assertTrue(all(path.is_dir() for path in check_dirs))
            self.assertFalse(any(item["action"] == "removed" for item in untouched["actions"]))

            inventory = build_tidy_inventory(repo=repo, home=home)
            result = apply_tidy(repo=repo, inventory=inventory, keep_checks=2)

            self.assertFalse(check_dirs[1].exists())
            self.assertFalse(check_dirs[2].exists())
            self.assertTrue(check_dirs[0].is_dir())
            self.assertTrue(check_dirs[3].is_dir())
            actions = {Path(item["path"]).name: item for item in result["actions"]}
            self.assertEqual(actions["check-01"]["action"], "removed")
            self.assertEqual(actions["check-02"]["action"], "removed")
            self.assertEqual(actions["check-00"]["reason"], "kept: item evidence")
            self.assertIn("newest 2", actions["check-03"]["reason"])

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
            self.assertEqual(missing_action["action"], "kept")
            registered = _git(repo, "worktree", "list", "--porcelain")
            self.assertIn(str(missing), registered)


if __name__ == "__main__":
    unittest.main()
