import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from depone.agent_fabric.team_ledger import build_team_ledger_verdict

from witnessd.fanin import run_team
from witnessd.signing import gen_operator_keypair


_HAS_OPENSSL = shutil.which("openssl") is not None


def _seed_repo(repo: Path, *, conflict: bool = False) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w16"], cwd=repo, check=True)
    (repo / "pkg").mkdir()
    shared = "left\nmiddle\nright\n" if not conflict else "same\n"
    (repo / "pkg" / "shared.py").write_text(shared, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
class TestW16MergeLanes(unittest.TestCase):
    def _run(self, lane_specs: list[dict], *, conflict: bool = False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "repo"
        out_dir = root / "evidence"
        keys = root / "keys"
        repo.mkdir()
        keys.mkdir()
        base_commit = _seed_repo(repo, conflict=conflict)
        private_key_path, public_key_path = gen_operator_keypair(str(keys))
        return run_team(
            lane_specs,
            repo_root=str(repo),
            out_dir=str(out_dir),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
            max_parallel=2,
            merge_groups=[
                {
                    "lane_id": "merge-ab",
                    "sources": ["lane-a", "lane-b"],
                    "files": ["pkg/shared.py"],
                }
            ],
        )

    def test_overlapping_sources_emit_depone_valid_merge_attempt_receipt(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/shared.py"],
                    "commands": [
                        [
                            "sh",
                            "-c",
                            "python3 - <<'PY'\n"
                            "from pathlib import Path\n"
                            "p=Path('pkg/shared.py')\n"
                            "p.write_text(p.read_text().replace('left','lane-a'), encoding='utf-8')\n"
                            "PY",
                        ]
                    ],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/shared.py"],
                    "commands": [
                        [
                            "sh",
                            "-c",
                            "python3 - <<'PY'\n"
                            "from pathlib import Path\n"
                            "p=Path('pkg/shared.py')\n"
                            "p.write_text(p.read_text().replace('right','lane-b'), encoding='utf-8')\n"
                            "PY",
                        ]
                    ],
                },
            ]
        )

        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        self.assertEqual(ledger["merge_receipt"], "merge-ab/team-merge-attempt-receipt.json")
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(
            verdict["overlapping_touched_files"][0]["lane_ids"],
            ["lane-a", "lane-b"],
        )

        schedule = json.loads((result["base_dir"] / ledger["schedule_receipt"]).read_text())
        intervals = {lane["lane_id"]: lane for lane in schedule["lanes"]}
        self.assertGreaterEqual(
            intervals["merge-ab"]["spawned_monotonic_ns"],
            intervals["lane-a"]["exited_monotonic_ns"],
        )
        self.assertGreaterEqual(
            intervals["merge-ab"]["spawned_monotonic_ns"],
            intervals["lane-b"]["exited_monotonic_ns"],
        )
        self.assertEqual(ledger["lanes"][-1]["lane_id"], "merge-ab")
        self.assertEqual(ledger["lanes"][-1]["touched_files"], ["merge/merge-ab.txt"])

    def test_unresolved_conflict_is_blocked_with_conflict_bytes(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/shared.py"],
                    "commands": [["sh", "-c", "printf 'lane-a\\n' > pkg/shared.py"]],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/shared.py"],
                    "commands": [["sh", "-c", "printf 'lane-b\\n' > pkg/shared.py"]],
                },
            ],
            conflict=True,
        )

        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
        self.assertEqual(lanes["merge-ab"]["verification_state"], "blocked")
        self.assertEqual(
            lanes["merge-ab"]["blocked_reason"],
            "ERR_TEAM_MERGE_CONFLICT_UNRESOLVED",
        )
        self.assertNotIn("merge_receipt", ledger)
        conflict_bytes = result["base_dir"] / "merge-ab" / "conflicts" / "pkg" / "shared.py"
        self.assertTrue(conflict_bytes.is_file())
        self.assertIn("<<<<<<<", conflict_bytes.read_text(encoding="utf-8"))

    def test_merge_attempt_producer_failure_is_not_reported_as_source_conflict(self):
        blocked_receipt = {
            "kind": "depone-team-merge-attempt",
            "schema_version": "0.1",
            "decision": "blocked",
            "base_commit": "0" * 40,
            "head_commits": ["1" * 40, "2" * 40],
            "attempt_worktree": "unavailable",
            "dirty_target_refused": False,
            "exit_code": 127,
            "merged_files": [],
            "conflict_files": [],
            "cleanup": {"attempt_worktree_removed": True},
            "captured_at": "2026-07-03T00:00:00Z",
            "source_command": ["python3", "-m", "depone", "team-merge-attempt"],
            "errors": [
                {
                    "code": "ERR_TEAM_MERGE_ATTEMPT_FAILED",
                    "message": "producer unavailable",
                }
            ],
            "boundary": {
                "executes_git_merge_attempt": True,
                "launches_agents": False,
                "calls_live_models": False,
                "approves_merge": False,
                "raises_assurance": False,
            },
        }
        with patch("witnessd.fanin._build_team_merge_attempt_receipt", return_value=blocked_receipt):
            result = self._run(
                [
                    {
                        "lane_id": "lane-a",
                        "region": ["pkg/shared.py"],
                        "commands": [["sh", "-c", "printf 'lane-a\\n' > pkg/shared.py"]],
                    },
                    {
                        "lane_id": "lane-b",
                        "region": ["pkg/shared.py"],
                        "commands": [["sh", "-c", "printf 'lane-b\\n' > pkg/shared.py"]],
                    },
                ],
                conflict=True,
            )

        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
        self.assertEqual(lanes["merge-ab"]["verification_state"], "blocked")
        self.assertEqual(lanes["merge-ab"]["blocked_reason"], "ERR_TEAM_MERGE_ATTEMPT_FAILED")
        self.assertNotIn("merge_receipt", ledger)
        self.assertFalse((result["base_dir"] / "merge-ab" / "conflicts").exists())


if __name__ == "__main__":
    unittest.main()
