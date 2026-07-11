"""Live smoke for ORRO review-only role lanes against the real agy CLI.

This test exercises the production reviewer-lane surface end to end:
flowplan model policy -> review-only role-lane-plan -> `orro review` ->
run_agy_review_lane(model=...) -> review receipt and advisory model
declaration.

Skipped unless both a real `agy` binary is on PATH and WITNESSD_LIVE_AGY_SMOKE=1
is set, because this can hit a real paid API and must not run implicitly in CI.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main

_LIVE_GATE = (
    shutil.which("agy") is not None and os.environ.get("WITNESSD_LIVE_AGY_SMOKE") == "1"
)
_SKIP_REASON = "set WITNESSD_LIVE_AGY_SMOKE=1 with a real agy binary on PATH to run"


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "orro-review-smoke@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Review Smoke"],
        cwd=repo,
        check=True,
    )
    (repo / "calc.py").write_text(
        "def average(nums):\n"
        "    return sum(nums) / len(nums)  # bug: empty list\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


@unittest.skipUnless(_LIVE_GATE, _SKIP_REASON)
class OrroReviewLiveSmokeTests(unittest.TestCase):
    def test_real_agy_runs_policy_reviewer_lane_and_stays_read_only(self) -> None:
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
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "orro",
                            "flowplan",
                            "review calc.py for bugs",
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
                    ),
                    0,
                )
            role_lanes = json.loads(role_lanes_out.read_text(encoding="utf-8"))
            role_lanes["lanes"][0]["prompt"] = (
                "Review calc.py for correctness bugs. Do not edit any files."
            )
            role_lanes_out.write_text(
                json.dumps(role_lanes, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
                        "--timeout-seconds",
                        "180",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            lane = payload["lanes"][0]
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(lane["adapter"], "agy")
            self.assertEqual(lane["model"], "gemini-3.1-pro")
            self.assertEqual(lane["touched_files"], [])
            self.assertEqual(lane["review_receipt"]["kind"], "moonweave-review-receipt")
            self.assertEqual(
                lane["review_receipt"]["can_change_evidence_verdict"],
                False,
            )
            self.assertEqual(
                lane["model_declaration"]["verification_status"],
                "requested-unverified",
            )
            self.assertTrue(
                (Path(payload["run_dir"]) / lane["lane_id"] / "review-receipt.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
