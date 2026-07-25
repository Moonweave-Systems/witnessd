from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.cli.status import _suggested_step_command
from witnessd.orro_next import decide_next
from witnessd.orro_report import build_report, render_text_report
from witnessd.orro_ship import (
    _commit_status_command,
    _dirty_blocker,
    _github_repo,
    _post_commit_status,
    _status_description,
    _suggested_branch,
    build_ship,
    ship_run,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def _depone_root() -> Path:
    configured = os.environ.get("WITNESSD_DEPONE_ROOT")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parents[1].parent
    for name in ("depone", "Depone"):
        candidate = root / name
        if (candidate / "depone").is_dir():
            return candidate
    raise RuntimeError("tests require WITNESSD_DEPONE_ROOT or a sibling Depone checkout")


def _seed_repo(repo: Path, *, branch: str = "feat/change", content: str = "tracked\n") -> None:
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")
    _git(repo, "checkout", "-q", "-b", branch)


def _real_run(root: Path, repo: Path, *, goal: str = "ship test") -> tuple[Path, Path]:
    home = root / "home"
    run = root / "run"
    sandbox = root / "sandbox"
    run.mkdir()
    sandbox.mkdir()
    def invoke(argv: list[str]) -> dict:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv)
        payload = json.loads(output.getvalue())
        if code != 0:
            raise AssertionError(payload)
        return payload
    invoke(["init", "--home", str(home), "--repo", str(repo), "--depone-root", str(_depone_root())])
    workflow = run / "workflow-plan.json"
    lanes = run / "role-lane-plan.json"
    invoke(["flowplan", goal, "--root", str(repo), "--profile", "verification-only", "--out", str(workflow), "--role-lanes-out", str(lanes), "--lane-adapter", "shell", "--check", "true", "--json"])
    invoke(["proofrun", goal, "--repo", str(repo), "--home", str(home), "--workflow-plan", str(workflow), "--role-lane-plan", str(lanes), "--adapter", "shell", "--runner-sandbox", str(sandbox), "--run-dir", str(run), "--json"])
    invoke(["proofcheck", "--evidence-dir", str(run), "--home", str(home), "--out", str(run / "proofcheck-verdict.json"), "--json"])
    invoke(["handoff", str(run), "--home", str(home), "--out", str(run / "orro-handoff.json"), "--json"])
    return home, run


class OrroShipTest(unittest.TestCase):
    def test_github_remote_parser_supports_ssh_and_https_and_rejects_other_urls(self) -> None:
        self.assertEqual(_github_repo("git@github.com:owner/repo.git"), ("owner", "repo"))
        self.assertEqual(_github_repo("https://github.com/owner/repo"), ("owner", "repo"))
        self.assertIsNone(_github_repo("https://gitlab.com/owner/repo.git"))
        self.assertIsNone(_github_repo("not a remote URL"))

    def test_commit_status_payload_and_argv_are_pinned(self) -> None:
        description = _status_description("pass", "base123456789", "head123456789")
        argv = _commit_status_command("owner", "repo", "head123", description, "https://github.com/owner/repo/pull/1")
        self.assertEqual(
            argv,
            [
                "gh",
                "api",
                "repos/owner/repo/statuses/head123",
                "--method",
                "POST",
                "-f",
                "state=success",
                "-f",
                "context=ORRO guardrail receipt",
                "-f",
                f"description={description}",
                "-f",
                "target_url=https://github.com/owner/repo/pull/1",
            ],
        )
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> object:
            calls.append(command)
            return type("Completed", (), {"returncode": 0, "stdout": '{"url": "https://api.github.com/repos/owner/repo/statuses/1", "context": "ORRO guardrail receipt", "state": "success"}', "stderr": ""})()

        result, error = _post_commit_status(
            "https://github.com/owner/repo.git", "head123", description,
            target_url="https://github.com/owner/repo/pull/1", runner=runner
        )
        self.assertIsNone(error)
        self.assertEqual(result, {"url": "https://api.github.com/repos/owner/repo/statuses/1", "context": "ORRO guardrail receipt", "state": "success"})
        self.assertEqual(calls, [argv])

    def test_commit_status_description_stays_under_github_limit(self) -> None:
        description = _status_description("pass", "a" * 64, "b" * 64, goal="goal " + "x" * 500)
        self.assertLess(len(description), 140)

    def test_dirty_status_document_advises_committing_only_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            _seed_repo(repo)
            status = repo / ".orro" / "STATUS.md"
            status.parent.mkdir()
            status.write_text("status\n", encoding="utf-8")
            _git(repo, "add", ".orro/STATUS.md")
            _git(repo, "commit", "-qm", "add status")
            status.write_text("changed status\n", encoding="utf-8")
            blocker = _dirty_blocker(Path(directory) / "run", repo, Path(directory) / "home")
            self.assertEqual(
                blocker["next_commands"],
                ["git add .orro/STATUS.md", "git commit -m 'update ORRO status'"],
            )

    def test_dirty_internal_artifacts_advise_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            (repo / ".orro").mkdir()
            (repo / ".orro" / "run.json").write_text("{}\n", encoding="utf-8")
            (repo / ".witnessd").mkdir()
            (repo / ".witnessd" / "state.json").write_text("{}\n", encoding="utf-8")
            blocker = _dirty_blocker(root / "run", repo, repo / ".witnessd")
            self.assertEqual(blocker["next_commands"][0], "Add these exact lines to .gitignore:")
            self.assertIn(".witnessd/", blocker["next_commands"])
            self.assertIn(".orro/", blocker["next_commands"])

    def test_dirty_source_tree_keeps_generic_commit_advice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            blocker = _dirty_blocker(root / "run", repo, root / "home")
            self.assertEqual(blocker["next_commands"], ["git add -A", "git commit -m 'ship run'"])

    def _remote(self, root: Path, repo: Path) -> Path:
        bare = root / "bare.git"
        _git(root, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return bare

    def test_real_chain_is_required_for_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            home, run = _real_run(root, repo)
            self._remote(root, repo)
            code, payload = ship_run(run, home=home, repo=repo)
            self.assertEqual(code, 0, payload)
            receipt = payload["ship_receipt"]
            self.assertIsNone(receipt["commit_status"])
            self.assertTrue(receipt["commit_status_error"])
            self.assertEqual(receipt["observed_base_commit"], json.loads((run / "team-ledger.json").read_text())["start_commit"])
            self.assertEqual(receipt["pushed_head_commit"], _git(repo, "rev-parse", "HEAD"))
            self.assertIn("commits added after the observed run are NOT covered", _read_pr_body(receipt))
            self.assertIn("feat/change", _git(root / "bare.git", "branch"))

    def test_forged_three_file_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            self._remote(root, repo)
            run = root / "forged"
            run.mkdir()
            (run / "workflow-plan.json").write_text(json.dumps({"goal": "forge", "profile": "code-change"}))
            (run / "proofcheck-verdict.json").write_text(json.dumps({"decision": "pass", "orro_binding": {}}))
            (run / "orro-handoff.json").write_text(json.dumps({"kind": "orro-handoff", "evidence_dir": str(run), "decision_refs": []}))
            code, payload = build_ship(run, home=root / "missing-home", repo=repo)
            self.assertEqual(code, 1)
            self.assertEqual(payload["blockers"][0]["code"], "ERR_ORRO_SHIP_RUN_EVIDENCE_MISSING")
            self.assertNotIn("refs/heads/feat/change", subprocess.run(["git", "show-ref"], cwd=root / "bare.git", capture_output=True, text=True).stdout)

    def test_cross_repo_evidence_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            _seed_repo(repo_a)
            home, run = _real_run(root, repo_a)
            _seed_repo(repo_b, content="different repository\n")
            self._remote(root, repo_b)
            code, payload = build_ship(run, home=home, repo=repo_b)
            self.assertEqual(code, 1)
            self.assertEqual(payload["blockers"][0]["code"], "ERR_ORRO_SHIP_EVIDENCE_REPO_MISMATCH")
            self.assertIn("different repository", payload["blockers"][0]["message"])
            self.assertNotIn("refs/heads/feat/change", subprocess.run(["git", "show-ref"], cwd=root / "bare.git", capture_output=True, text=True).stdout)

    def _assert_paste_safe(self, command: str, expected: list[str]) -> None:
        tokens = shlex.split(command)
        self.assertEqual(tokens, expected)
        self.assertEqual([token for token in tokens if token in {"&&", "||", ";", "|", "&"}], [], command)

    def test_hostile_emitted_commands_are_single_token_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "MARK"
            hostile = f'x" y\' ; $(touch {marker}) `touch {marker}`\nnext'
            repo = root / "repo"
            _seed_repo(repo)
            hostile_home, hostile_run = _real_run(root, repo, goal=hostile)
            (repo / "dirty.txt").write_text("dirty\n")
            commands = build_ship(hostile_run, home=hostile_home, repo=repo)[1]["blockers"][0]["next_commands"]
            self._assert_paste_safe(commands[0], ["git", "add", "-A"])
            self._assert_paste_safe(commands[1], ["git", "commit", "-m", hostile])
            for command in commands:
                subprocess.run(command, cwd=repo, shell=True, check=False, capture_output=True)
            self.assertFalse(marker.exists())
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            _git(repo, "checkout", "-q", "--detach", "HEAD")
            blocker = build_ship(hostile_run, home=hostile_home, repo=repo)[1]["blockers"][0]
            self.assertEqual(blocker["code"], "ERR_ORRO_SHIP_BRANCH_REQUIRED")
            self._assert_paste_safe(blocker["next_commands"][0], ["git", "switch", "-c", _suggested_branch(hostile)])
            item = {"id": hostile, "title": hostile}
            step = {"id": hostile, "profile": "verification-only", "checks": [hostile]}
            command = _suggested_step_command(item, step, repo=hostile)
            self._assert_paste_safe(command, ["orro", "check", "--check", hostile, "--roadmap-item", hostile, "--roadmap-step", hostile, "--repo", hostile])

    def test_detached_head_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            home, run = _real_run(root, repo)
            self._remote(root, repo)
            _git(repo, "checkout", "-q", "--detach", "HEAD")
            code, payload = build_ship(run, home=home, repo=repo)
            self.assertEqual(code, 1)
            self.assertEqual(payload["blockers"][0]["code"], "ERR_ORRO_SHIP_BRANCH_REQUIRED")

    def test_next_and_report_never_call_evidence_missing_run_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            home, run = _real_run(root, repo)
            (run / "team-ledger.json").unlink()
            from witnessd.cli.verify import _proofcheck_binding
            verdict_path = run / "proofcheck-verdict.json"
            verdict = json.loads(verdict_path.read_text())
            verdict["orro_binding"] = _proofcheck_binding(run)
            verdict_path.write_text(json.dumps(verdict))
            handoff = json.loads((run / "orro-handoff.json").read_text())
            handoff["decision_refs"][0]["sha256"] = __import__("hashlib").sha256(verdict_path.read_bytes()).hexdigest()
            (run / "orro-handoff.json").write_text(json.dumps(handoff))
            code, continuation = decide_next(run, home=home)
            self.assertEqual(code, 1)
            self.assertEqual(continuation["error"]["code"], "ERR_ORRO_NEXT_EVIDENCE_MISSING")
            _, report = build_report(run, home=home)
            text = render_text_report(report)
            self.assertNotIn("State: complete", text)
            self.assertNotIn("ship the branch; merge stays human", text)

    def test_symlinked_verdict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            home, run = _real_run(root, repo)
            outside = root / "verdict.json"
            outside.write_bytes((run / "proofcheck-verdict.json").read_bytes())
            (run / "proofcheck-verdict.json").unlink()
            (run / "proofcheck-verdict.json").symlink_to(outside)
            code, payload = build_ship(run, home=home, repo=repo)
            self.assertEqual(code, 1)
            self.assertEqual(payload["blockers"][0]["code"], "ERR_ORRO_SHIP_EVIDENCE_INVALID")

    def test_push_receipt_failure_reports_remote_push(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            _seed_repo(repo)
            home, run = _real_run(root, repo)
            self._remote(root, repo)
            bad_receipt = root / "receipt-dir"
            bad_receipt.mkdir()
            code, payload = ship_run(run, home=home, repo=repo, receipt_path=bad_receipt)
            self.assertEqual(code, 1)
            self.assertFalse(payload["blocked"])
            self.assertTrue(payload["pushed"])
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_SHIP_POST_PUSH_FAILED")


def _read_pr_body(receipt: dict) -> str:
    command = receipt["pr_command"]
    return command.split(" --body ", 1)[1] if " --body " in command else ""


if __name__ == "__main__":
    unittest.main()
