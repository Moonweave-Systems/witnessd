from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "orro-review@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Review"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("# review fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _fake_agy(directory: Path) -> str:
    path = directory / "agy"
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$AGY_ARGV_CAPTURE"\n'
        "if [ -t 1 ]; then\n"
        "  printf '%s\\n' 'Review findings:'\n"
        "  printf '%s\\n' 'low README.md:1 review-only smoke finding'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class OrroReviewTests(unittest.TestCase):
    def test_orro_review_runs_policy_resolved_agy_lane_without_assurance(self) -> None:
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
                            str(_depone_root()),
                        ]
                    ),
                    0,
                )

            role_lanes_out = root / "role-lane-plan.json"
            with redirect_stdout(io.StringIO()) as flow_stdout:
                flow_code = main(
                    [
                        "orro",
                        "flowplan",
                        "review the readme",
                        "--root",
                        str(repo),
                        "--profile",
                        "review-only",
                        "--role-lanes-out",
                        str(role_lanes_out),
                        "--model-policy",
                        "default",
                        "--role-lane-tier",
                        "frontier",
                    ]
                )
            self.assertEqual(flow_code, 0, flow_stdout.getvalue())

            role_lanes = json.loads(role_lanes_out.read_text(encoding="utf-8"))
            reviewer_lane = role_lanes["lanes"][0]
            self.assertEqual(reviewer_lane["phase"], "review")
            self.assertEqual(reviewer_lane["adapter"], "agy")
            self.assertEqual(reviewer_lane["model"], "gemini-3.5-flash")
            self.assertEqual(reviewer_lane["region"], ["."])

            bindir = root / "bin"
            bindir.mkdir()
            argv_capture = root / "agy-argv.txt"
            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"AGY_ARGV_CAPTURE": str(argv_capture)}),
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "orro",
                        "review",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--role-lane-plan",
                        str(role_lanes_out),
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-review-summary")
            self.assertEqual(payload["can_change_evidence_verdict"], False)
            self.assertEqual(payload["raises_assurance"], False)
            self.assertEqual(payload["executes_proofrun"], False)
            self.assertEqual(payload["verifies_evidence"], False)
            self.assertEqual(payload["workflow_profile"], "review-only")
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(len(payload["lanes"]), 1)

            lane = payload["lanes"][0]
            self.assertEqual(lane["lane_id"], reviewer_lane["lane_id"])
            self.assertEqual(lane["adapter"], "agy")
            self.assertEqual(lane["model"], "gemini-3.5-flash")
            self.assertEqual(lane["touched_files"], [])
            self.assertEqual(lane["review_receipt"]["kind"], "moonweave-review-receipt")
            self.assertEqual(lane["review_receipt"]["can_change_evidence_verdict"], False)
            self.assertEqual(
                lane["model_declaration"]["verification_status"],
                "requested-unverified",
            )

            argv = argv_capture.read_text(encoding="utf-8")
            self.assertIn("--model\n", argv)
            self.assertIn("gemini-3.5-flash\n", argv)
            run_dir = Path(payload["run_dir"])
            self.assertTrue((run_dir / "orro-review-summary.json").is_file())
            self.assertTrue((run_dir / reviewer_lane["lane_id"] / "review-receipt.json").is_file())
            self.assertFalse((run_dir / "team-ledger.json").exists())


if __name__ == "__main__":
    unittest.main()
