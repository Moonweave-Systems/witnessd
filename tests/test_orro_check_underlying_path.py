from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _run(argv: list[str]) -> tuple[int, object, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    stdout = out.getvalue()
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_raw": stdout}
    return code, payload, err.getvalue()


class VerificationOnlyPathTest(unittest.TestCase):
    """Characterize the raw path a companion would reuse:
    init -> flowplan(verification-only, --check) -> proofrun(shell) -> proofcheck.
    Proves it reaches Depone decision=pass with NO --write-scope and NO AI adapter.
    """

    def test_shell_checks_reach_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _seed_repo(repo)
            home = root / "home"
            run_dir = root / "run"
            sandbox = root / "sandbox"
            sandbox.mkdir()
            wp = run_dir / "workflow-plan.json"
            rlp = run_dir / "role-lane-plan.json"
            verdict = run_dir / "proofcheck-verdict.json"
            run_dir.mkdir()
            goal = "verify current working tree"

            # init provisions home/provision.json (pinned Depone) — required before proofcheck.
            code, _, err = _run(["init", "--home", str(home), "--repo", str(repo)])
            self.assertEqual(code, 0, f"init failed: {err}")
            self.assertTrue(
                (home / "provision.json").is_file(), "init did not provision home"
            )

            code, _, err = _run(
                [
                    "flowplan",
                    goal,
                    "--root",
                    str(repo),
                    "--profile",
                    "verification-only",
                    "--out",
                    str(wp),
                    "--role-lanes-out",
                    str(rlp),
                    "--lane-adapter",
                    "shell",
                    "--check",
                    "true",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, f"flowplan failed: {err}")
            plan = json.loads(rlp.read_text(encoding="utf-8"))
            self.assertTrue(plan.get("lanes"), "no lanes compiled")
            for lane in plan["lanes"]:
                self.assertEqual(lane["adapter"], "shell", lane)
                self.assertEqual(lane["lane_intent"], "verification-only", lane)
                self.assertEqual(lane["region"], [], lane)

            code, _, err = _run(
                [
                    "proofrun",
                    goal,
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                    "--workflow-plan",
                    str(wp),
                    "--role-lane-plan",
                    str(rlp),
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    str(sandbox),
                    "--run-dir",
                    str(run_dir),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, f"proofrun failed: {err}")
            self.assertTrue((run_dir / "team-ledger.json").is_file())

            code, payload, err = _run(
                [
                    "proofcheck",
                    "--evidence-dir",
                    str(run_dir),
                    "--home",
                    str(home),
                    "--out",
                    str(verdict),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, f"proofcheck failed: {err}")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)  # narrow for type checkers
            self.assertEqual(
                payload.get("decision"),
                "pass",
                f"expected pass verdict, got: {payload}",
            )


if __name__ == "__main__":
    unittest.main()
