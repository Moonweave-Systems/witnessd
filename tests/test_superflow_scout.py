from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "scout@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "superflow scout"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Scout Fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: scout-fixture\n---\n# Skill\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / "witnessd").mkdir()
    (repo / "witnessd" / "__main__.py").write_text("# cli\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_fixture.py").write_text("# tests\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


class SuperflowScoutTests(unittest.TestCase):
    def test_scout_writes_read_only_planning_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            repo.mkdir()
            _seed_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "scout",
                        "verify scout artifacts",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            run_dir = Path(payload["run_dir"])
            self.assertEqual(payload["decision"], "scouted")
            self.assertNotIn("verification_receipt", payload)
            for name in (
                "repo-profile.json",
                "context-pack.json",
                "discovery-notes.md",
                "lane-context.json",
                "skillpack-lock.json",
                "verification-recipe.json",
                "mcp-tool-receipt-fake.json",
                "pr-handoff.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            self.assertFalse((run_dir / "verification-receipt.json").exists())

            context = json.loads((run_dir / "context-pack.json").read_text(encoding="utf-8"))
            self.assertIn("README.md", context["selected_paths"])
            self.assertTrue((run_dir / "skillpacks" / "skill-copy.md").is_file())

            handoff = json.loads((run_dir / "pr-handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(handoff["verification_receipt_hashes"], [])
            self.assertIn("no commands were executed", handoff["unresolved_risks"][0])

            proofcheck = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "depone",
                    "proofcheck",
                    "--evidence-dir",
                    str(run_dir),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proofcheck.returncode, 0)
            verdict = json.loads(proofcheck.stdout)
            self.assertEqual(verdict["decision"], "blocked")
            self.assertIn(
                "ERR_SUPERFLOW_ARTIFACT_REQUIRED_MISSING",
                {error["code"] for error in verdict["errors"]},
            )

    def test_superflow_scout_alias_routes_to_scout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["superflow", "scout", "alias goal", "--repo", str(repo)])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["decision"], "scouted")

    def test_orro_scout_alias_routes_to_scout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "scout", "alias goal", "--repo", str(repo)])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["decision"], "scouted")

    def test_flowplan_alias_routes_to_plan_only_surface(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["flowplan", "plan the next ORRO wave", "--root", "."])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sealed_plan"]["goal"], "plan the next ORRO wave")
        self.assertEqual(payload["draft_events"], [])
        self.assertNotIn("run_dir", payload)
        self.assertNotIn("team_ledger", payload)
        self.assertNotIn("team_ledger_verdict", payload)

    def test_orro_flowplan_alias_routes_to_plan_only_surface(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "flowplan", "plan the next ORRO wave", "--root", "."])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sealed_plan"]["goal"], "plan the next ORRO wave")
        self.assertEqual(payload["draft_events"], [])
        self.assertNotIn("run_dir", payload)
        self.assertNotIn("team_ledger", payload)
        self.assertNotIn("team_ledger_verdict", payload)

    def test_flowplan_rejects_draft_adapter_worker_path(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["flowplan", "plan only", "--draft-adapter", "codex"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --draft-adapter", stderr.getvalue())

    def test_legacy_plan_keeps_draft_adapter_compatibility(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plan", "legacy draft path", "--draft-adapter", "codex"])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sealed_plan"]["goal"], "legacy draft path")
        self.assertEqual(payload["draft_events"][0]["adapter"], "codex")


if __name__ == "__main__":
    unittest.main()
