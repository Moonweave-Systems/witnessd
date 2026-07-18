import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.team_ledger import build_team_ledger_verdict
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.evidence_contract import validate_evidence_contract

from witnessd.fanin import run_team
from witnessd.runlog import verify_runlog
from witnessd.signing import gen_operator_keypair
from witnessd.canonical import canonical_hash


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


def _evidence_context_from_dir(
    root: Path,
    *,
    trusted_observer_public_key_file: str | None = None,
) -> EvidenceContext:
    files = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        files.append(
            EvidenceFile(
                path=path.name,
                content=content,
                sha256=canonical_hash(content),
            )
        )
    raw = (
        {"trusted_observer_public_key_file": trusted_observer_public_key_file}
        if trusted_observer_public_key_file is not None
        else {}
    )
    return EvidenceContext(run_id=root.name, files=files, raw=raw)


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
        result = run_team(
            lane_specs,
            repo_root=str(repo),
            out_dir=str(out_dir),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
        )
        result["public_key_path"] = public_key_path
        return result

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
            lane_dir = result["base_dir"] / lane["lane_id"]
            run_intent = json.loads(
                (lane_dir / "run-intent.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_intent["schema_version"], "1.0")
            contract = json.loads(
                (lane_dir / "evidence-contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(contract["schema_version"], "v105.verify_wedge")
            self.assertNotIn("role_capability_write_scope", contract)
        self.assertEqual(verify_runlog(result["runlog"])["ok"], True)

    def test_shell_lane_emits_only_declared_lane_intent(self):
        result = self._run(
            [
                {
                    "lane_id": "declared-lane",
                    "lane_intent": "verification-only",
                    "region": ["pkg/declared.py"],
                    "commands": [["sh", "-c", "true"]],
                },
                {
                    "lane_id": "undeclared-lane",
                    "region": ["pkg/undeclared.py"],
                    "commands": [
                        [
                            "sh",
                            "-c",
                            "mkdir -p pkg && echo implementation > pkg/undeclared.py",
                        ]
                    ],
                },
            ]
        )

        lanes = {lane["lane_id"]: lane for lane in result["ledger"]["lanes"]}
        self.assertEqual(
            lanes["declared-lane"]["lane_intent"], "verification-only"
        )
        self.assertNotIn("lane_intent", lanes["undeclared-lane"])

    def test_w3_companion_artifacts_are_audited_in_team_runlog(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/a.py"]],
                },
            ]
        )

        artifacts = [
            event["payload"]["artifact"]
            for event in result["runlog"]
            if event["event"] == "emit-artifact"
        ]

        self.assertIn("team-ledger.json", artifacts)
        self.assertIn("lane-a/worktree-lane-receipt.json", artifacts)
        self.assertIn("lane-a/evidence-next-verdict.json", artifacts)

    def test_shell_lane_emits_write_scope_declaration_when_supplied(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "write_scope": ["pkg/**"],
                    "role_id": "runner",
                    "role_capability": "execute",
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/a.py"]],
                },
            ]
        )

        lane_dir = result["base_dir"] / "lane-a"
        declaration = json.loads(
            (lane_dir / "write-scope-declaration.json").read_text(encoding="utf-8")
        )
        self.assertEqual(declaration["kind"], "moonweave-write-scope-declaration")
        self.assertEqual(declaration["declared_write_scope"], ["pkg/**"])
        self.assertEqual(declaration["allowed_touched_files"], ["pkg/a.py"])
        self.assertEqual(declaration["touched_files"], ["pkg/a.py"])
        self.assertEqual(declaration["verification_status"], "verified")
        self.assertEqual(declaration["conformance"], "pass")
        subject_names = [
            item["name"]
            for item in json.loads((lane_dir / "bundle.json").read_text(encoding="utf-8"))[
                "statement"
            ]["predicate"]["artifact_index"]
        ]
        self.assertIn("write-scope-declaration", subject_names)
        self.assertIn("git-diff-name-only.txt", subject_names)

        contract = json.loads(
            (lane_dir / "evidence-contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["schema_version"], "v109.role_capability_write_scope"
        )
        self.assertEqual(
            contract["role_capability_write_scope"],
            {
                "run_intent_path": "run-intent.json",
                "bundle_path": "bundle.json",
            },
        )
        self.assertEqual(
            validate_evidence_contract(
                _evidence_context_from_dir(
                    lane_dir,
                    trusted_observer_public_key_file=result["public_key_path"],
                )
            ),
            [],
        )

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
