import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.team_ledger import (
    build_team_ledger_verdict,
    validate_team_ledger,
)

from witnessd.team_ledger import (
    build_evidence_next_verdict,
    build_team_ledger,
    build_team_ledger_merge_receipt,
)
from witnessd.worktree import build_worktree_lane_receipt


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


def _make_lane(base_dir: Path, lane_id: str, touched_file: str) -> dict:
    repo = base_dir / f"{lane_id}-repo"
    repo.mkdir()
    start_commit = _seed_repo(repo)
    (repo / touched_file).parent.mkdir(parents=True, exist_ok=True)
    (repo / touched_file).write_text(f"{lane_id}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", f"{lane_id} change"], cwd=repo, check=True)
    end_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    evidence_dir = Path(lane_id)
    receipt_path = evidence_dir / "worktree-lane-receipt.json"
    verdict_path = evidence_dir / "evidence-next-verdict.json"
    (base_dir / evidence_dir).mkdir()
    receipt = build_worktree_lane_receipt(
        worktree=str(repo),
        base_commit=start_commit,
        evidence_dir=evidence_dir.as_posix(),
        commands=[{"command": "python3 -m unittest", "exit_code": 0}],
    )
    (base_dir / receipt_path).write_text(json.dumps(receipt), encoding="utf-8")
    (base_dir / verdict_path).write_text(
        json.dumps(build_evidence_next_verdict()), encoding="utf-8"
    )
    return {
        "lane_id": lane_id,
        "objective": f"{lane_id} objective",
        "start_commit": start_commit,
        "end_commit": end_commit,
        "evidence_dir": evidence_dir.as_posix(),
        "env_kind": "local",
        "runner_adapter_kind": "shell",
        "team_adapter_kind": "shell",
        "verification_state": "pass",
        "touched_files": [touched_file],
        "worktree_receipt": receipt_path.as_posix(),
        "evidence_next_verdict": verdict_path.as_posix(),
    }


def _make_verification_lane(
    base_dir: Path, lane_id: str, touched_file: str | None = None
) -> dict:
    repo = base_dir / f"{lane_id}-repo"
    repo.mkdir()
    start_commit = _seed_repo(repo)
    if touched_file is not None:
        (repo / touched_file).parent.mkdir(parents=True, exist_ok=True)
        (repo / touched_file).write_text(f"{lane_id}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", f"{lane_id} change"], cwd=repo, check=True
        )
    end_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    evidence_dir = Path(lane_id)
    receipt_path = evidence_dir / "worktree-lane-receipt.json"
    verdict_path = evidence_dir / "evidence-next-verdict.json"
    (base_dir / evidence_dir).mkdir()
    receipt = build_worktree_lane_receipt(
        worktree=str(repo),
        base_commit=start_commit,
        evidence_dir=evidence_dir.as_posix(),
        commands=[{"command": "python3 -m unittest", "exit_code": 0}],
    )
    (base_dir / receipt_path).write_text(json.dumps(receipt), encoding="utf-8")
    (base_dir / verdict_path).write_text(
        json.dumps(build_evidence_next_verdict()), encoding="utf-8"
    )
    return {
        "lane_id": lane_id,
        "lane_intent": "verification-only",
        "objective": f"{lane_id} objective",
        "start_commit": start_commit,
        "end_commit": end_commit,
        "evidence_dir": evidence_dir.as_posix(),
        "env_kind": "local",
        "runner_adapter_kind": "shell",
        "team_adapter_kind": "shell",
        "verification_state": "pass",
        "touched_files": [] if touched_file is None else [touched_file],
        "worktree_receipt": receipt_path.as_posix(),
        "evidence_next_verdict": verdict_path.as_posix(),
    }


class TestTeamLedger(unittest.TestCase):
    def test_disjoint_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            lane_a = _make_lane(base_dir, "lane-a", "pkg/a.py")
            lane_b = _make_lane(base_dir, "lane-b", "pkg/b.py")

            ledger = build_team_ledger(
                leader_objective="ship W3",
                leader_id="leader-fixed",
                start_commit=lane_a["start_commit"],
                stop_rule="all lanes pass or block",
                lanes=[lane_a, lane_b],
            )
            verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)

            self.assertEqual(verdict["decision"], "pass")
            self.assertEqual(verdict["overlapping_touched_files"], [])
            self.assertEqual(validate_team_ledger(ledger, base_dir=base_dir), [])
            self.assertIs(verdict["boundary"]["raises_assurance"], False)
            self.assertIs(verdict["boundary"]["approves_merge"], False)

    def test_overlap_without_merge_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            lane_a = _make_lane(base_dir, "lane-a", "pkg/shared.py")
            lane_b = _make_lane(base_dir, "lane-b", "pkg/shared.py")
            ledger = build_team_ledger(
                leader_objective="ship W3",
                leader_id="leader-fixed",
                start_commit=lane_a["start_commit"],
                stop_rule="all lanes pass or block",
                lanes=[lane_a, lane_b],
            )

            verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)

            self.assertEqual(verdict["decision"], "blocked")
            codes = {error["code"] for error in verdict["errors"]}
            self.assertIn("ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED", codes)
            self.assertTrue(verdict["overlapping_touched_files"])

    def test_overlap_with_passing_merge_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            lane_a = _make_lane(base_dir, "lane-a", "pkg/shared.py")
            lane_b = _make_lane(base_dir, "lane-b", "pkg/shared.py")
            merge_path = Path("merge-receipt.json")
            (base_dir / merge_path).write_text(
                json.dumps(
                    build_team_ledger_merge_receipt(
                        lanes=["lane-a", "lane-b"],
                        files=["pkg/shared.py"],
                        decision="pass",
                    )
                ),
                encoding="utf-8",
            )
            ledger = build_team_ledger(
                leader_objective="ship W3",
                leader_id="leader-fixed",
                start_commit=lane_a["start_commit"],
                stop_rule="all lanes pass or block",
                lanes=[lane_a, lane_b],
                merge_receipt=merge_path.as_posix(),
            )

            verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)

            self.assertEqual(verdict["decision"], "pass")

    def test_declared_verification_only_lane_passes_with_empty_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            lane = _make_verification_lane(base_dir, "verification-lane")
            ledger = build_team_ledger(
                leader_objective="verify W3",
                leader_id="leader-fixed",
                start_commit=lane["start_commit"],
                stop_rule="all lanes pass or block",
                lanes=[lane],
            )

            verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)

            self.assertEqual(verdict["decision"], "pass")
            codes = {error["code"] for error in verdict["errors"]}
            self.assertNotIn("ERR_TEAM_LEDGER_TOUCHED_FILES_REQUIRED", codes)

    def test_declared_verification_only_lane_with_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            lane = _make_verification_lane(
                base_dir, "verification-lane", "pkg/mutated.py"
            )
            ledger = build_team_ledger(
                leader_objective="verify W3",
                leader_id="leader-fixed",
                start_commit=lane["start_commit"],
                stop_rule="all lanes pass or block",
                lanes=[lane],
            )

            verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)

            self.assertEqual(verdict["decision"], "blocked")
            codes = {error["code"] for error in verdict["errors"]}
            self.assertIn("ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED", codes)


if __name__ == "__main__":
    unittest.main()
