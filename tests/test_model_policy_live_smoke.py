"""Live smoke test for the model-routing policy layer against a real CLI.

This is deliberately NOT a hand-assembled team-run bypass: it exercises the
actual production chain end to end --
compile_role_lane_plan(policy=DEFAULT_MODEL_POLICY) -> role-lane-plan lane
carrying a policy-resolved (adapter, model) -> `orro proofrun` ->
_role_lane_plan_team_specs -> run_team -> fanin's per-lane executor -> the
real adapter's `model=` argv -- so a bug in any single link (the policy
resolver, the lane compiler, the team-spec translation, or the fanin wiring)
would show up here even though each link also has its own fake-binary unit
test.

Only the runner/codex case is live-tested here: code-change role-lane-plans
can execute through `orro proofrun`. There is deliberately no reviewer/agy
live run in this file -- see the note at the bottom of this module for why.

Skipped unless both the codex binary is on PATH and
WITNESSD_LIVE_CODEX_SMOKE=1 is set, matching every other live smoke test in
this suite -- this hits a real paid API.

Run locally with:
  WITNESSD_LIVE_CODEX_SMOKE=1 python3 -m unittest tests.test_model_policy_live_smoke
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main

_CODEX_LIVE_GATE = (
    shutil.which("codex") is not None
    and os.environ.get("WITNESSD_LIVE_CODEX_SMOKE") == "1"
)
_AGY_LIVE_GATE = (
    shutil.which("agy") is not None and os.environ.get("WITNESSD_LIVE_AGY_SMOKE") == "1"
)


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "policy-smoke@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "policy-smoke"], cwd=repo, check=True)
    (repo / "README.md").write_text("# policy smoke fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@unittest.skipUnless(
    _CODEX_LIVE_GATE, "set WITNESSD_LIVE_CODEX_SMOKE=1 with a real codex binary on PATH"
)
class TestModelPolicyLiveSmokeRunnerLane(unittest.TestCase):
    def test_real_codex_runner_lane_receives_policy_resolved_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            repo.mkdir()
            _seed_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
                    stdout.getvalue(),
                )

            plan_out = root / "workflow-plan.json"
            role_lanes_out = root / "role-lane-plan.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "flowplan",
                        "review the readme",
                        "--root",
                        str(repo),
                        "--profile",
                        "code-change",
                        "--out",
                        str(plan_out),
                        "--role-lanes-out",
                        str(role_lanes_out),
                        "--model-policy",
                        "default",
                        "--role-lane-tier",
                        "frontier",
                    ]
                )
            self.assertEqual(code, 0, stdout.getvalue())
            role_lanes = json.loads(role_lanes_out.read_text(encoding="utf-8"))
            runner_lane = role_lanes["lanes"][0]
            self.assertEqual(runner_lane["adapter"], "codex")
            self.assertEqual(runner_lane["model"], "gpt-5.5")

            # The heuristic flowplan compiler's auto-generated prompt never
            # names the placeholder region file it allows the lane to touch
            # ("Execute ORRO role runner for goal: ..."), so a real codex
            # correctly does nothing and the ledger fails closed on "no
            # touched files" -- a real, pre-existing planner gap (documented
            # in project memory as "placeholder lane" aspirational UX), not
            # a model-routing bug. Patching only the prompt to actually name
            # the already-compiled, policy-resolved region lets this test
            # observe the real chain (policy -> lane.model ->
            # _role_lane_plan_team_specs -> fanin -> real codex) reach a full
            # passing Depone verdict, instead of stopping short at a known,
            # unrelated gap this task is not scoped to fix.
            region_path = runner_lane["region"][0]
            role_lanes["lanes"][0]["prompt"] = (
                f"Create the file {region_path} with the single line: ok"
            )
            role_lanes_out.write_text(
                json.dumps(role_lanes, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "review the readme",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(plan_out),
                        "--role-lane-plan",
                        str(role_lanes_out),
                        "--max-parallel",
                        "1",
                    ]
                )
            self.assertEqual(
                code, 0, f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
            )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            run_dir = Path(payload["run_dir"])
            ledger = json.loads((run_dir / "team-ledger.json").read_text())
            lane_id = ledger["lanes"][0]["lane_id"]
            receipt = json.loads(
                (run_dir / lane_id / "runner-receipt.json").read_text()
            )
            self.assertIn("-m", receipt["invocation"])
            self.assertIn("gpt-5.5", receipt["invocation"])
            declaration = json.loads(
                (run_dir / lane_id / "model-declaration.json").read_text()
            )
            self.assertEqual(declaration["adapter"], "codex")
            self.assertEqual(declaration["requested_model"], "gpt-5.5")
            self.assertEqual(declaration["verification_status"], "verified")


# There is deliberately no reviewer/agy live run here. compile_role_lane_plan
# resolving a reviewer role to agy/gemini-3.1-pro is covered by a fake-only
# unit test (test_orro_workflow.py::
# test_flowplan_role_lanes_model_policy_default_resolves_reviewer_to_agy),
# and agy's --model wiring itself is already live-verified independently in
# test_agy_live_smoke.py (PR #49) -- this policy layer reuses that same,
# adapter-agnostic model= plumbing (_role_lane_plan_team_specs ->
# fanin._run_adapter_lane -> run_adapter_lane(model=...)), which is already
# proven live end to end by the runner/codex case above.
#
# What is NOT live-tested is running a reviewer role-lane-plan lane through
# `run_team`: OwnershipRegistry.claim() (witnessd/lock.py) rejects
# region == ["."], which is exactly what every reviewer lane declares
# (review is whole-repo and read-only, so it has no specific file to own).
# This is not a bug to fix by loosening region-claim -- the write-lane path
# (proofrun/run_team + region locking) is simply the wrong shape for a
# review lane in the first place. The decided direction (a follow-up wave,
# not this change) is to wire reviewer role-lane-plan lanes through the
# existing read-only advisory review path instead:
# `run_agy_review_lane` -> review-receipt.json (can_change_evidence_verdict:
# false, non-assurance), called directly rather than through
# run_team/OwnershipRegistry. That path never touches region-claim or the
# proofrun phase-gate at all, so it needs no lock.py change, and it
# preserves the "review-only does not execute" invariant rather than
# bending it: review-receipt is already a non-assurance advisory artifact,
# so nothing about that invariant is in tension with it.


if __name__ == "__main__":
    unittest.main()
