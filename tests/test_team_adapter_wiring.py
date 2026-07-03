import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.__main__ import _parse_team_lane
from witnessd.fanin import run_team
from witnessd.signing import gen_operator_keypair


class TestTeamAdapterLaneParsing(unittest.TestCase):
    def test_parse_adapter_lane_with_prompt_and_region(self):
        self.assertEqual(
            _parse_team_lane(
                "L1:adapter=codex:tier=agentic:region=a.txt,b.txt:prompt=do X"
            ),
            {
                "lane_id": "L1",
                "adapter": "codex",
                "tier": "agentic",
                "region": ["a.txt", "b.txt"],
                "allowed_touched_files": ["a.txt", "b.txt"],
                "prompt": "do X",
            },
        )

    def test_parse_legacy_lane_keeps_placeholder_command(self):
        parsed = _parse_team_lane("L1:a.txt,b.txt")

        self.assertEqual(parsed["lane_id"], "L1")
        self.assertEqual(parsed["region"], ["a.txt", "b.txt"])
        self.assertNotIn("adapter", parsed)
        self.assertEqual(len(parsed["commands"]), 1)

    def test_parse_rejects_unknown_adapter(self):
        with self.assertRaisesRegex(ValueError, "ERR_TEAM_LANE_ADAPTER"):
            _parse_team_lane("L1:adapter=frobnicate:region=a.txt:prompt=do X")

    def test_parse_rejects_adapter_without_prompt(self):
        with self.assertRaisesRegex(ValueError, "ERR_TEAM_LANE_PROMPT"):
            _parse_team_lane("L1:adapter=codex:tier=agentic:region=a.txt")


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w7"], cwd=repo, check=True)
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
class TestTeamAdapterFanin(unittest.TestCase):
    def _run(self, lane_specs):
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

    def test_adapter_lane_uses_lane_worktree_as_sandbox(self):
        with tempfile.TemporaryDirectory() as bindir:
            result = self._run(
                [
                    {
                        "lane_id": "agent-lane",
                        "adapter": "codex",
                        "tier": "agentic",
                        "region": ["pkg/agent.py"],
                        "prompt": "write agent",
                        "codex_binary": _fake_codex(bindir),
                    }
                ]
            )

        lane = result["lanes"][0]
        worktree = Path(lane["worktree"])
        self.assertEqual(worktree.parent.name, "worktrees")
        self.assertTrue(worktree.name.startswith("agent-lane-"))
        self.assertNotEqual(os.path.abspath(lane["worktree"]), os.path.abspath(lane["evidence_dir"]))
        Path(lane["evidence_dir"]).resolve().relative_to(result["base_dir"].resolve())
        self.assertEqual(result["ledger"]["lanes"][0]["runner_adapter_kind"], "codex")
        self.assertEqual(result["ledger"]["lanes"][0]["touched_files"], ["pkg/agent.py"])

    def test_blocked_adapter_lane_is_fail_closed_and_other_lanes_continue(self):
        result = self._run(
            [
                {
                    "lane_id": "blocked-lane",
                    "adapter": "codex",
                    "tier": "agentic",
                    "region": ["pkg/blocked.py"],
                    "prompt": "write blocked",
                    "budget": {"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 0},
                },
                {
                    "lane_id": "shell-lane",
                    "region": ["pkg/shell.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo shell > pkg/shell.py"]],
                },
            ]
        )

        lanes = {lane["lane_id"]: lane for lane in result["ledger"]["lanes"]}
        self.assertEqual(lanes["blocked-lane"]["verification_state"], "blocked")
        self.assertEqual(lanes["blocked-lane"]["blocked_reason"], "budget_exceeded")
        self.assertEqual(lanes["shell-lane"]["verification_state"], "pass")
        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        self.assertEqual(len(ledger["lanes"]), 2)


def _fake_codex(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "mkdir -p pkg\n"
        "echo agent > pkg/agent.py\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_touches_outside_region(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "mkdir -p pkg\n"
        "echo outside > pkg/outside.py\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo done >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestTeamAdapterLedgerContract(unittest.TestCase):
    def test_mixed_shell_and_codex_team_ledger_passes_depone_verdict(self):
        from depone.agent_fabric.paired_run import validate_runner_receipt
        from depone.agent_fabric.team_ledger import build_team_ledger_verdict

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as bindir:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            keys = root / "keys"
            repo.mkdir()
            keys.mkdir()
            base_commit = _seed_repo(repo)
            private_key_path, public_key_path = gen_operator_keypair(str(keys))
            result = run_team(
                [
                    {
                        "lane_id": "shell-lane",
                        "region": ["pkg/shell.py"],
                        "commands": [["sh", "-c", "mkdir -p pkg && echo shell > pkg/shell.py"]],
                    },
                    {
                        "lane_id": "codex-lane",
                        "adapter": "codex",
                        "tier": "quick",
                        "region": ["pkg/agent.py"],
                        "prompt": "write agent",
                        "codex_binary": _fake_codex(bindir),
                    },
                ],
                repo_root=str(repo),
                out_dir=str(out_dir),
                private_key_path=private_key_path,
                public_key_path=public_key_path,
                base_commit=base_commit,
            )

            verdict = build_team_ledger_verdict(result["ledger"], base_dir=result["base_dir"])
            self.assertEqual(verdict["decision"], "pass")
            kinds = {lane["lane_id"]: lane["runner_adapter_kind"] for lane in result["ledger"]["lanes"]}
            self.assertEqual(kinds, {"shell-lane": "shell", "codex-lane": "codex"})
            receipt = json.loads(
                (result["base_dir"] / "codex-lane" / "runner-receipt.json").read_text()
            )
            self.assertEqual(validate_runner_receipt(receipt), [])
            self.assertEqual(receipt["runner_kind"], "codex-cli")

    def test_cli_adapter_lane_region_bounds_capture_manifest(self):
        from depone.agent_fabric.capture_bridge import validate_capture_manifest

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as bindir:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            keys = root / "keys"
            repo.mkdir()
            keys.mkdir()
            base_commit = _seed_repo(repo)
            private_key_path, public_key_path = gen_operator_keypair(str(keys))
            lane = _parse_team_lane(
                "codex-lane:adapter=codex:tier=quick:region=pkg/allowed.py:prompt=write outside"
            )
            lane["codex_binary"] = _fake_codex_touches_outside_region(bindir)
            result = run_team(
                [lane],
                repo_root=str(repo),
                out_dir=str(out_dir),
                private_key_path=private_key_path,
                public_key_path=public_key_path,
                base_commit=base_commit,
            )

            manifest = result["lanes"][0]["manifest"]
            self.assertEqual(manifest["allowed_touched_files"], ["pkg/allowed.py"])
            errors = validate_capture_manifest(manifest)
            self.assertTrue(
                any("unexpected touched files" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
