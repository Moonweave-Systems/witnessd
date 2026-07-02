import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.team_ledger import build_team_ledger_verdict

from witnessd.fanin import run_team
from witnessd.runlog import verify_runlog
from witnessd.signing import gen_operator_keypair


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w3"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestTeamFanin(unittest.TestCase):
    def _run(self, lane_specs: list[dict]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "repo"
        out_dir = root / "evidence"
        keys = root / "keys"
        repo.mkdir()
        keys.mkdir()
        base_commit = _seed_repo(repo)
        private_key_path, public_key_path = gen_operator_keypair(str(keys))
        return run_team(
            lane_specs,
            repo_root=str(repo),
            out_dir=str(out_dir),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
        )

    def test_disjoint_write_lanes_emit_passing_team_ledger(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/a.py"]],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/b.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo b > pkg/b.py"]],
                },
            ]
        )

        verdict = build_team_ledger_verdict(
            result["ledger"], base_dir=result["base_dir"]
        )

        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(verdict["overlapping_touched_files"], [])
        self.assertEqual([lane["lane_id"] for lane in result["ledger"]["lanes"]], ["lane-a", "lane-b"])
        for lane in result["lanes"]:
            self.assertEqual(validate_capture_manifest(lane["manifest"]), [])
        self.assertEqual(verify_runlog(result["runlog"])["ok"], True)

    def test_claim_conflict_is_audited_and_conflicting_lane_is_excluded(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/shared.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/shared.py"]],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/shared.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo b > pkg/shared.py"]],
                },
            ]
        )

        self.assertEqual([lane["lane_id"] for lane in result["ledger"]["lanes"]], ["lane-a"])
        events = result["runlog"]
        self.assertIn("claim-conflict", [event["event"] for event in events])
        conflict = next(event for event in events if event["event"] == "claim-conflict")
        self.assertEqual(conflict["error_code"], "ERR_REGION_CLAIM_CONFLICT")
        self.assertEqual(conflict["payload"]["lane_id"], "lane-b")

    def test_read_only_lane_is_audited_but_not_in_merge_ledger(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/a.py"]],
                },
                {
                    "lane_id": "lane-ro",
                    "region": [],
                    "commands": [["sh", "-c", "true"]],
                },
            ]
        )

        self.assertEqual([lane["lane_id"] for lane in result["ledger"]["lanes"]], ["lane-a"])
        self.assertIn("read-only-lane-audit", [event["event"] for event in result["runlog"]])
        verdict = build_team_ledger_verdict(
            result["ledger"], base_dir=result["base_dir"]
        )
        self.assertEqual(verdict["decision"], "pass")


if __name__ == "__main__":
    unittest.main()
