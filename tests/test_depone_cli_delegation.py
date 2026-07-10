"""In-process coverage for Depone's deprecated shims delegating to a real witnessd.

`depone.agent_fabric.codex_local_capability` / `team_shell_lane_launch` /
`team_worktree_prep` are compatibility surfaces that Depone kept after its
Phase 4 extraction (`Extract deprecated execution surfaces to witnessd`); they
raise `ERR_DEPONE_EXECUTION_SURFACE_MOVED_TO_WITNESSD` when witnessd is
unavailable, so Depone's own standalone test suite cannot exercise the
delegated behavior. witnessd is the one repo where both sides are guaranteed
present, so the delegation path is tested here instead. See
depone/docs/phase2-tcb-extraction.md.

These call Depone's shim functions directly (plain Python, in-process) rather
than spawning `depone` as a subprocess. A subprocess needs its own PYTHONPATH
wired to find *both* depone and witnessd, and a previous version of this file
that spawned `python -m depone ...` broke exactly that way in CI: it only
worked locally because witnessd happened to be editable-installed there. An
in-process call shares this test process's own sys.path — witnessd via
cwd-based sys.path[0] (every other witnessd test already relies on this for
`import witnessd`), depone via the PYTHONPATH env var CI already sets — so
there is no subprocess-boundary case left to get wrong. Same pattern
test_depone_replica_conformance.py's test_codex_capability_matches_depone_for_missing_binary
already uses.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.codex_local_capability import (
    build_codex_local_capability,
    write_codex_local_capability,
)
from depone.agent_fabric.team_launch_preflight import (
    TEAM_LAUNCH_PREFLIGHT_KIND,
    TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION,
)
from depone.agent_fabric.team_shell_lane_launch import (
    run_shell_lane_command,
    _self_test as team_shell_lane_launch_self_test,
)
from depone.agent_fabric.team_worktree_prep import (
    build_team_worktree_prep,
    _self_test as team_worktree_prep_self_test,
)


class DeponeShimDelegationTests(unittest.TestCase):
    def test_codex_local_capability_delegates_pass_receipt_for_fake_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            (root / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'codex 0.cli\\n'\n", encoding="utf-8"
            )
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "AGENTS.md", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            receipt = build_codex_local_capability(
                repo=root,
                codex_binary=str(fake_codex),
                instruction_files=[Path("AGENTS.md")],
            )
            out = root / "capability.json"
            write_codex_local_capability(out, receipt)
            loaded = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["adapter"]["version"], "codex 0.cli")
        self.assertEqual(loaded["decision"], "pass")

    def test_team_shell_lane_launch_self_test_delegates_to_witnessd(self) -> None:
        # No exception means the shim's _self_test() reached witnessd's real
        # _self_test() and it passed; a WitnessdUnavailableError or a
        # witnessd-side AssertionError would fail this test.
        team_shell_lane_launch_self_test()

    def test_team_shell_lane_launch_runs_allowlisted_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = run_shell_lane_command(
                allowlist={
                    "commands": [
                        {
                            "id": "fixture-echo",
                            "argv": ["true"],
                        }
                    ]
                },
                command_id="fixture-echo",
                cwd=root,
                transcript_path=root / "transcript.json",
                agent_role_id="worker",
            )

        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["boundary"]["uses_shell"], False)

    def test_team_worktree_prep_self_test_delegates_to_witnessd(self) -> None:
        team_worktree_prep_self_test()

    def test_team_worktree_prep_creates_lane_via_witnessd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "r@x.invalid"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "prep-test"], cwd=repo, check=True
            )
            (repo / "sample.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            preflight = {
                "kind": TEAM_LAUNCH_PREFLIGHT_KIND,
                "schema_version": TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION,
                "decision": "pass",
                "launch_intent": "plan-only",
                "base_commit": base,
                "lane_count": 1,
                "lanes": [
                    {
                        "lane_id": "lane-1",
                        "planned_worktree": "lane-1",
                        "evidence_dir": "lane-1",
                        "worktree_receipt": "lane-1/worktree-receipt.json",
                    }
                ],
                "boundary": {
                    "launches_agents": False,
                    "creates_worktrees": False,
                    "executes_commands": False,
                    "mutates_worktree": False,
                    "calls_live_models": False,
                    "raises_assurance": False,
                },
                "errors": [],
            }

            receipt = build_team_worktree_prep(
                preflight,
                repo_root=repo,
                worktree_root=root / "worktrees",
                create_worktree=True,
            )

        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["lanes"][0]["action"], "created")


if __name__ == "__main__":
    unittest.main()
