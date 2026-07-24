from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.orro_ship import build_ship, ship_run
from witnessd.orro_auto import build_auto_plan
from witnessd.orro_next import decide_next


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def _ready_run(root: Path) -> Path:
    run = root / "run"
    run.mkdir()
    (run / "workflow-plan.json").write_text(
        json.dumps({"goal": "ship the change", "profile": "code-change"})
    )
    binding = {
        "kind": "orro-proofcheck-binding",
        "schema_version": "1.0",
        "evidence_dir": str(run),
        "artifact_hashes": [
            {
                "path": "workflow-plan.json",
                "sha256": hashlib.sha256((run / "workflow-plan.json").read_bytes()).hexdigest(),
            }
        ],
    }
    verdict = {"decision": "pass", "orro_binding": binding}
    verdict_path = run / "proofcheck-verdict.json"
    verdict_path.write_text(json.dumps(verdict))
    (run / "orro-handoff.json").write_text(
        json.dumps(
            {
                "kind": "orro-handoff",
                "evidence_dir": str(run),
                "decision_refs": [
                    {
                        "path": "proofcheck-verdict.json",
                        "sha256": hashlib.sha256(verdict_path.read_bytes()).hexdigest(),
                        "decision": "pass",
                    }
                ],
            }
        )
    )
    return run


class OrroShipTest(unittest.TestCase):
    def _repo(self, root: Path, *, branch: str = "feat/change") -> Path:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "tracked.txt").write_text("tracked\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")
        _git(repo, "checkout", "-b", branch)
        return repo

    def test_each_ship_precondition_returns_structured_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            run = root / "run"
            result = build_ship(run, home=root / "home", repo=repo)
            self.assertEqual(result[0], 1)
            self.assertEqual(result[1]["blockers"][0]["code"], "ERR_ORRO_SHIP_PROOFCHECK_REQUIRED")
            self.assertTrue(result[1]["blockers"][0]["next_commands"])

            ready = _ready_run(root)
            verdict = json.loads((ready / "proofcheck-verdict.json").read_text())
            verdict["decision"] = "fail"
            (ready / "proofcheck-verdict.json").write_text(json.dumps(verdict))
            result = build_ship(ready, home=root / "home", repo=repo)
            self.assertEqual(result[1]["blockers"][0]["code"], "ERR_ORRO_SHIP_PROOFCHECK_NOT_PASS")
            restored = {"decision": "pass", "orro_binding": verdict["orro_binding"]}
            verdict_path = ready / "proofcheck-verdict.json"
            verdict_path.write_text(json.dumps(restored))
            handoff = json.loads((ready / "orro-handoff.json").read_text())
            handoff["decision_refs"][0]["sha256"] = hashlib.sha256(verdict_path.read_bytes()).hexdigest()
            (ready / "orro-handoff.json").write_text(json.dumps(handoff))
            (repo / "dirty.txt").write_text("dirty\n")
            result = build_ship(ready, home=root / "home", repo=repo)
            self.assertEqual(result[1]["blockers"][0]["code"], "ERR_ORRO_SHIP_WORKTREE_DIRTY")
            self.assertEqual(result[1]["blockers"][0]["next_commands"], ['git add -A && git commit -m "ship the change"'])

            _git(repo, "add", "dirty.txt")
            _git(repo, "commit", "-m", "clean")
            bare = root / "default.git"
            _git(root, "init", "--bare", str(bare))
            _git(repo, "remote", "add", "origin", str(bare))
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            _git(repo, "checkout", "main")
            result = build_ship(ready, home=root / "home", repo=repo)
            self.assertEqual(result[1]["blockers"][0]["code"], "ERR_ORRO_SHIP_DEFAULT_BRANCH")
            self.assertIn("git checkout -b", result[1]["blockers"][0]["next_commands"][0])

            _git(repo, "checkout", "feat/change")
            _git(repo, "remote", "remove", "origin")
            result = build_ship(ready, home=root / "home", repo=repo)
            self.assertEqual(result[1]["blockers"][0]["code"], "ERR_ORRO_SHIP_REMOTE_REQUIRED")

    def test_ship_pushes_bare_remote_and_seals_receipt_without_gh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            bare = root / "bare.git"
            _git(root, "init", "--bare", str(bare))
            _git(repo, "remote", "add", "origin", str(bare))
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            run = _ready_run(root)
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
                code, payload = ship_run(run, home=root / "home", repo=repo)
            self.assertEqual(code, 0)
            self.assertIsNone(payload["ship_receipt"]["pr_url"])
            self.assertIn("gh pr create", payload["ship_receipt"]["pr_command"])
            self.assertEqual(payload["ship_receipt"]["boundary"]["merges"], False)
            self.assertIn("feat/change", _git(bare, "branch"))
            self.assertTrue((run / "ship-receipt.json").is_file())
            self.assertEqual(build_ship(run, home=root / "home", repo=repo)[0], 0)

    def test_completed_bound_run_reports_ship_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = _ready_run(root)
            code, continuation = decide_next(run, home=root / "home")
            self.assertEqual(code, 0)
            self.assertTrue(continuation["ship_ready"])
            self.assertEqual(continuation["ship_command"], f"orro ship {run} --home {root / 'home'}")
            code, plan = build_auto_plan(run, home=root / "home")
            self.assertEqual(code, 0)
            self.assertEqual(plan["next_allowed"], [continuation["ship_command"]])


if __name__ == "__main__":
    unittest.main()
