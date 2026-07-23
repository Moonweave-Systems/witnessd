from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


class OrroDiagnosticsTests(unittest.TestCase):
    def _init_home(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "init",
                        "--home",
                        str(home),
                        "--repo",
                        str(repo),
                        "--depone-root",
                        os.environ.get(
                            "WITNESSD_DEPONE_ROOT",
                            str(Path(__file__).resolve().parents[2] / "depone"),
                        ),
                    ]
                ),
                0,
            )
        return repo, home

    def test_proofcheck_surfaces_untracked_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            evidence_dir = home / "runs" / "proofrun"
            evidence_dir.mkdir(parents=True)
            (repo / "evidence.txt").write_text("local only\n", encoding="utf-8")
            (evidence_dir / "team-ledger.json").write_text(
                json.dumps(
                    {
                        "repo_root": str(repo),
                        "lanes": [
                            {
                                "lane_id": "lane-a",
                                "worktree": "worktrees/lane-a",
                                "evidence_dir": "lane-a",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def fake_depone(*_args, **_kwargs):
                return 1, {
                    "command": "proofcheck",
                    "verifier_command": "proofcheck",
                    "decision": "blocked",
                    "error_count": 1,
                    "errors": [
                        {
                            "code": "ERR_ORRO_REQUIRED_TEST_EVIDENCE_MISSING",
                            "message": "required evidence missing: evidence.txt",
                            "evidence_path": "evidence.txt",
                        }
                    ],
                }

            stdout = io.StringIO()
            with patch("witnessd.cli.verify._run_depone_json", side_effect=fake_depone):
                with redirect_stdout(stdout):
                    code = main(
                        ["proofcheck", str(evidence_dir), "--home", str(home), "--json"]
                    )

            self.assertEqual(code, 1)
            error = json.loads(stdout.getvalue())["error"]
            self.assertEqual(
                error["code"], "ERR_ORRO_UNTRACKED_EVIDENCE_NOT_IN_ISOLATED_WORKTREE"
            )
            self.assertIn("exists locally but is untracked", error["message"])
            self.assertIn("git status --short -- evidence.txt", error["next_command"])
            self.assertIn("git add -- evidence.txt", error["next_command"])


if __name__ == "__main__":
    unittest.main()
