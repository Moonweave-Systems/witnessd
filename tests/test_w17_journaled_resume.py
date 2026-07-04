import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.team_ledger import build_team_ledger_verdict

from witnessd.__main__ import main
from witnessd.fanin import _lane_control_stem, resume_team, run_team
from witnessd.signing import gen_operator_keypair


_HAS_OPENSSL = shutil.which("openssl") is not None


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w17"], cwd=repo, check=True)
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


@unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
class TestW17JournaledResume(unittest.TestCase):
    def _run_initial(self, lane_specs: list[dict]):
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
        result = run_team(
            lane_specs,
            repo_root=str(repo),
            out_dir=str(out_dir),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
            run_id="team-run",
            max_parallel=2,
        )
        result["repo"] = repo
        result["keys"] = keys
        return result

    def test_resume_skips_only_rederived_pass_and_reruns_missing_lane_result(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/b.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo b > pkg/b.py"]],
                },
            ]
        )
        result_path = result["base_dir"] / ".lane-exec" / f"{_lane_control_stem('lane-b')}-result.json"
        result_path.unlink()

        resumed = resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=2)

        ledger = resumed["ledger"]
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(ledger["resume_receipt"], "team-resume-receipt.json")
        lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
        self.assertEqual(lanes["lane-a"]["evidence_dir"], "lane-a")
        self.assertEqual(lanes["lane-b"]["evidence_dir"], "attempts/attempt-2/lane-b")
        self.assertTrue((result["base_dir"] / "lane-b" / "capture-manifest.json").is_file())
        self.assertTrue((result["base_dir"] / "attempts" / "attempt-2" / "lane-b").is_dir())

        receipt = json.loads((result["base_dir"] / ledger["resume_receipt"]).read_text())
        decisions = {decision["lane_id"]: decision for decision in receipt["decisions"]}
        self.assertEqual(decisions["lane-a"]["decision"], "skipped_as_proven")
        self.assertEqual(decisions["lane-a"]["attempt"], 1)
        self.assertEqual(decisions["lane-b"]["decision"], "re_executed")
        self.assertEqual(decisions["lane-b"]["attempt"], 2)
        self.assertEqual([item["attempt"] for item in decisions["lane-b"]["attempts"]], [1, 2])

    def test_resume_reruns_tampered_completed_lane_instead_of_trusting_journal(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ]
        )
        verdict_path = result["base_dir"] / "lane-a" / "evidence-next-verdict.json"
        verdict_path.write_text(
            json.dumps(
                {
                    "command": "evidence-next",
                    "decision": "blocked",
                    "blocking_reasons": ["tampered after journal claimed completion"],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        resumed = resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=1)

        ledger = resumed["ledger"]
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")
        lane = ledger["lanes"][0]
        self.assertEqual(lane["lane_id"], "lane-a")
        self.assertEqual(lane["evidence_dir"], "attempts/attempt-2/lane-a")
        receipt = json.loads((result["base_dir"] / ledger["resume_receipt"]).read_text())
        self.assertEqual(receipt["decisions"][0]["decision"], "re_executed")
        self.assertFalse(receipt["boundary"]["trusts_journal_completion"])
        self.assertTrue(receipt["boundary"]["skip_requires_rederivation"])

    def test_team_resume_cli_writes_resume_receipt(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ]
        )

        code = main(["team", "resume", str(result["base_dir"]), "--run-id", "team-run"])

        self.assertEqual(code, 0)
        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        self.assertEqual(ledger["resume_receipt"], "team-resume-receipt.json")
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")

    def test_resume_fails_closed_on_malformed_lane_control(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/b.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo b > pkg/b.py"]],
                },
            ]
        )
        control_path = result["base_dir"] / ".lane-exec" / f"{_lane_control_stem('lane-b')}.json"
        control_path.write_text('{"lane_id":"lane-b","spec":', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "ERR_TEAM_RESUME_CONTROL_INVALID"):
            resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=2)

    def test_resume_rederives_newest_successful_attempt_without_rerunning_again(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ]
        )
        verdict_path = result["base_dir"] / "lane-a" / "evidence-next-verdict.json"
        verdict_path.write_text(
            json.dumps(
                {
                    "command": "evidence-next",
                    "decision": "blocked",
                    "blocking_reasons": ["tampered after journal claimed completion"],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=1)

        resumed = resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=1)

        self.assertFalse((result["base_dir"] / "attempts" / "attempt-3").exists())
        ledger = resumed["ledger"]
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")
        lane = ledger["lanes"][0]
        self.assertEqual(lane["evidence_dir"], "attempts/attempt-2/lane-a")
        receipt = json.loads((result["base_dir"] / ledger["resume_receipt"]).read_text())
        self.assertEqual(receipt["decisions"][0]["decision"], "skipped_as_proven")
        self.assertEqual(receipt["decisions"][0]["attempt"], 2)
        self.assertEqual(
            [item["status"] for item in receipt["decisions"][0]["attempts"]],
            ["indeterminate", "completed"],
        )

    def test_resume_does_not_fall_back_past_corrupted_newer_attempt(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ]
        )
        attempt_control = result["base_dir"] / "attempts" / "attempt-2" / ".lane-exec"
        attempt_control.mkdir(parents=True)
        result_path = attempt_control / f"{_lane_control_stem('lane-a')}-result.json"
        result_path.write_text('{"run_id":"team-run","lane":', encoding="utf-8")

        resumed = resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=1)

        self.assertTrue((result["base_dir"] / "attempts" / "attempt-3" / "lane-a").is_dir())
        lane = resumed["ledger"]["lanes"][0]
        self.assertEqual(lane["evidence_dir"], "attempts/attempt-3/lane-a")
        receipt = json.loads((result["base_dir"] / resumed["ledger"]["resume_receipt"]).read_text())
        self.assertEqual(receipt["decisions"][0]["decision"], "re_executed")
        self.assertEqual(receipt["decisions"][0]["attempt"], 3)

    def test_resume_does_not_fall_back_past_partial_newer_attempt_without_result(self):
        result = self._run_initial(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ]
        )
        partial_dir = result["base_dir"] / "attempts" / "attempt-2" / "lane-a"
        partial_dir.mkdir(parents=True)
        (partial_dir / "partial-marker.txt").write_text("partial\n", encoding="utf-8")

        resumed = resume_team(str(result["base_dir"]), run_id="team-run", max_parallel=1)

        self.assertTrue((result["base_dir"] / "attempts" / "attempt-3" / "lane-a").is_dir())
        lane = resumed["ledger"]["lanes"][0]
        self.assertEqual(lane["evidence_dir"], "attempts/attempt-3/lane-a")
        receipt = json.loads((result["base_dir"] / resumed["ledger"]["resume_receipt"]).read_text())
        self.assertEqual(receipt["decisions"][0]["decision"], "re_executed")
        self.assertEqual(receipt["decisions"][0]["attempt"], 3)


if __name__ == "__main__":
    unittest.main()
