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


class OrroCheckBlockerTest(unittest.TestCase):
    def test_no_checks_declared_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            code, payload, err = _run(
                ["orro", "check", "--repo", str(repo), "--json"]
            )
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(payload["kind"], "orro-companion-result")
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_CHECK_NO_CHECKS_DECLARED"
            )
            self.assertIn("required_input_or_grant", payload["error"])
            self.assertIn("next_command", payload["error"])


class OrroCheckVerifyTest(unittest.TestCase):
    def _run_check(
        self, tmp: str, checks: list[str]
    ) -> tuple[tuple[int, object, str], Path]:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        _seed_repo(repo)
        argv = [
            "orro",
            "check",
            "--repo",
            str(repo),
            "--home",
            str(root / "home"),
            "--run-dir",
            str(root / "run"),
            "--no-review",
            "--json",
        ]
        for check in checks:
            argv += ["--check", check]
        return _run(argv), root

    def test_passing_check_yields_pass_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (code, payload, err), root = self._run_check(tmp, ["true"])
            self.assertEqual(code, 0, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["kind"], "orro-companion-manifest")
            self.assertEqual(payload["scope"], "state-verified")
            self.assertIs(payload["reviewed_work_execution_observed"], False)
            self.assertIs(payload["verification_checks_executed_observed"], True)
            self.assertEqual(payload["execution_adapter_lanes_spawned"], 0)
            self.assertEqual(payload["verdict_ref"]["decision"], "pass")
            self.assertNotIn("review_ref", payload)
            manifest = json.loads(
                (root / "run" / "companion-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["verdict_ref"]["decision"], "pass")

    def test_failing_check_yields_blocked_verdict_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (code, payload, err), root = self._run_check(tmp, ["false"])
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["kind"], "orro-companion-manifest")
            self.assertIn(
                payload["verdict_ref"]["decision"],
                {"blocked", "blocked-explicit"},
            )
            self.assertIs(payload["reviewed_work_execution_observed"], False)
            self.assertTrue((root / "run" / "companion-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
