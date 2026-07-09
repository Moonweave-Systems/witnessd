import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "orro"], cwd=repo, check=True)
    (repo / "README.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


class OrroPublicFlowTests(unittest.TestCase):
    def test_orro_scout_is_non_executing_context_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _seed_repo(repo)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(["orro", "scout", "--repo", str(repo)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["command"], "orro scout")
            self.assertFalse(payload["executes_workers"])
            self.assertEqual(payload["verdict"], "evidence-pending")
            self.assertIn("README.txt", payload["files"])

    def test_orro_flowplan_emits_plan_without_worker_execution(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = main(["orro", "flowplan", "write two files", "--root", "."])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertIn("sealed_plan", payload)
        self.assertNotIn("run_dir", payload)
        self.assertNotIn("team_ledger_verdict", payload)

    def test_orro_proofrun_and_proofcheck_use_existing_engine_paths(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = Path(
            os.environ.get("WITNESSD_DEPONE_ROOT") or witnessd_root.parent / "depone"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            repo.mkdir()
            _seed_repo(repo)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "init",
                            "--home",
                            str(home),
                            "--depone-root",
                            str(depone_root),
                        ]
                    ),
                    0,
                )
            proofrun_out = io.StringIO()
            with redirect_stdout(proofrun_out):
                proofrun_code = main(
                    [
                        "orro",
                        "proofrun",
                        "write two independent files",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                    ]
                )
            self.assertEqual(proofrun_code, 0)
            run_dir = Path(json.loads(proofrun_out.getvalue())["run_dir"])
            (run_dir / "team-ledger-verdict.json").unlink()

            proofcheck_out = io.StringIO()
            with redirect_stdout(proofcheck_out):
                proofcheck_code = main(
                    ["orro", "proofcheck", str(run_dir), "--home", str(home)]
                )

            self.assertEqual(proofcheck_code, 0)
            payload = json.loads(proofcheck_out.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())

    def test_orro_handoff_requires_explicit_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                missing_code = main(
                    ["orro", "handoff", "--proofcheck-verdict", str(missing)]
                )
            self.assertEqual(missing_code, 2)
            self.assertIn(
                "ERR_ORRO_HANDOFF_PROOFCHECK_VERDICT_REQUIRED", stderr.getvalue()
            )

            failed = root / "failed.json"
            failed.write_text(json.dumps({"decision": "blocked"}), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                failed_code = main(
                    ["orro", "handoff", "--proofcheck-verdict", str(failed)]
                )
            self.assertEqual(failed_code, 1)
            self.assertIn("ERR_ORRO_HANDOFF_PROOFCHECK_NOT_PASSING", stderr.getvalue())

            passing = root / "proofcheck-verdict.json"
            passing.write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                passing_code = main(
                    ["orro", "handoff", "--proofcheck-verdict", str(passing)]
                )
            self.assertEqual(passing_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["command"], "orro handoff")
            self.assertEqual(payload["handoff_status"], "ready-for-human-review")
            self.assertFalse(payload["approves_merge"])
            self.assertFalse(payload["raises_assurance"])


if __name__ == "__main__":
    unittest.main()
