import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import _parse_team_lane, _parse_team_merge_group, main
from witnessd.eventlog import EventLog
from witnessd.fanin import DEFAULT_STOP_RULE, _run_adapter_lane, run_team
from witnessd.signing import gen_operator_keypair
from witnessd.team_ledger import build_team_ledger


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

    def test_parse_adapter_lane_carries_explicit_model(self):
        parsed = _parse_team_lane(
            "L1:adapter=codex:tier=frontier:region=a.txt:prompt=do X:model=gpt-5.5"
        )

        self.assertEqual(parsed["model"], "gpt-5.5")

    def test_parse_adapter_lane_without_model_omits_it(self):
        parsed = _parse_team_lane(
            "L1:adapter=codex:tier=agentic:region=a.txt:prompt=do X"
        )

        self.assertNotIn("model", parsed)

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

    def test_parse_team_merge_group(self):
        self.assertEqual(
            _parse_team_merge_group("merge-ab:lane-a,lane-b:pkg/shared.py"),
            {
                "lane_id": "merge-ab",
                "sources": ["lane-a", "lane-b"],
                "files": ["pkg/shared.py"],
            },
        )

    def test_parse_team_merge_group_rejects_single_source(self):
        with self.assertRaisesRegex(ValueError, "ERR_TEAM_MERGE_GROUP_FORMAT"):
            _parse_team_merge_group("merge-ab:lane-a:pkg/shared.py")


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
        self.assertNotEqual(
            os.path.abspath(lane["worktree"]), os.path.abspath(lane["evidence_dir"])
        )
        Path(lane["evidence_dir"]).resolve().relative_to(result["base_dir"].resolve())
        self.assertEqual(result["ledger"]["lanes"][0]["runner_adapter_kind"], "codex")
        self.assertEqual(
            result["ledger"]["lanes"][0]["touched_files"], ["pkg/agent.py"]
        )

    def test_glob_write_scope_region_reaches_claim_manifest_receipt_and_ledger(self):
        with tempfile.TemporaryDirectory() as bindir:
            result = self._run(
                [
                    {
                        "lane_id": "frontend-lane",
                        "adapter": "codex",
                        "tier": "agentic",
                        "region": ["frontend/**"],
                        "prompt": "write frontend",
                        "codex_binary": _fake_codex_frontend(bindir),
                        "write_scope": ["frontend/**"],
                        "role_id": "runner",
                        "role_capability": "execute",
                        "capture_profile": "full",
                    }
                ]
            )

        claim_events = [
            event
            for event in result["runlog"]
            if event["event"] == "region-claim"
            and event["payload"].get("lane_id") == "frontend-lane"
        ]
        self.assertEqual(claim_events[0]["payload"]["region"], ["frontend/**"])

        lane_dir = result["base_dir"] / "frontend-lane"
        manifest = json.loads((lane_dir / "capture-manifest.json").read_text())
        self.assertEqual(manifest["allowed_touched_files"], ["frontend/**"])

        worktree_receipt = json.loads(
            (lane_dir / "worktree-lane-receipt.json").read_text()
        )
        self.assertEqual(worktree_receipt["changed_files"], ["frontend/src/App.tsx"])

        ledger_lane = result["ledger"]["lanes"][0]
        self.assertEqual(ledger_lane["verification_state"], "pass")
        self.assertEqual(ledger_lane["touched_files"], ["frontend/src/App.tsx"])

        declaration = json.loads(
            (result["base_dir"] / "write-scope-declaration.json").read_text()
        )
        self.assertEqual(declaration["declared_write_scope"], ["frontend/**"])
        self.assertEqual(declaration["allowed_touched_files"], ["frontend/**"])
        self.assertEqual(declaration["touched_files"], ["frontend/src/App.tsx"])
        self.assertEqual(declaration["conformance"], "pass")

    def test_blocked_adapter_lane_is_fail_closed_and_other_lanes_continue(self):
        with tempfile.TemporaryDirectory() as bindir:
            result = self._run(
                [
                    {
                        "lane_id": "blocked-lane",
                        "adapter": "codex",
                        "tier": "agentic",
                        "region": ["pkg/blocked.py"],
                        "prompt": "write blocked",
                        "budget": {
                            "max_tokens": 10**9,
                            "max_usd": 10**9,
                            "max_depth": 0,
                        },
                        # Provide a fake binary so preflight passes deterministically
                        # (CI has no real `codex` on PATH); the depth-0 budget is
                        # what must block the lane, yielding budget_exceeded.
                        "codex_binary": _fake_codex(bindir),
                    },
                    {
                        "lane_id": "shell-lane",
                        "region": ["pkg/shell.py"],
                        "commands": [
                            ["sh", "-c", "mkdir -p pkg && echo shell > pkg/shell.py"]
                        ],
                    },
                ]
            )

        lanes = {lane["lane_id"]: lane for lane in result["ledger"]["lanes"]}
        self.assertEqual(lanes["blocked-lane"]["verification_state"], "blocked")
        self.assertEqual(lanes["blocked-lane"]["blocked_reason"], "budget_exceeded")
        self.assertEqual(lanes["shell-lane"]["verification_state"], "pass")
        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        self.assertEqual(len(ledger["lanes"]), 2)

    def test_committed_timeout_is_distinct_and_proofcheck_still_rejects(self):
        with tempfile.TemporaryDirectory() as bindir:
            result = self._run(
                [
                    {
                        "lane_id": "timeout-lane",
                        "adapter": "codex",
                        "tier": "agentic",
                        "region": ["pkg/timeout.py"],
                        "prompt": "write timeout",
                        "codex_binary": _fake_codex_timeout(bindir),
                        "timeout_seconds": 1,
                    }
                ]
            )

        lane = result["ledger"]["lanes"][0]
        self.assertEqual(lane["lane_id"], "timeout-lane")
        self.assertEqual(lane["runner_adapter_kind"], "codex")
        self.assertEqual(lane["verification_state"], "blocked")
        self.assertEqual(
            lane["blocked_reason"],
            "ERR_TEAM_LANE_TIMEOUT_COMMITTED_EVIDENCE_PENDING",
        )
        self.assertNotEqual(lane["start_commit"], lane["end_commit"])
        self.assertEqual(lane["touched_files"], ["pkg/timeout.py"])
        self.assertNotIn("evidence_next_verdict", lane)
        self.assertFalse(
            (result["base_dir"] / "timeout-lane" / "evidence-next-verdict.json").exists()
        )

        receipt = json.loads(
            (result["base_dir"] / "timeout-lane" / "runner-receipt.json").read_text()
        )
        self.assertEqual(receipt["exit_code"], 124)
        self.assertIs(receipt["timed_out"], True)
        command_log = json.loads(
            (result["base_dir"] / "adapter-command.json").read_text()
        )
        self.assertEqual(command_log["exit_code"], 124)

        depone_root = os.environ.get("WITNESSD_DEPONE_ROOT")
        if depone_root:
            pythonpath = os.pathsep.join(
                part for part in (depone_root, os.environ.get("PYTHONPATH")) if part
            )
            proofcheck_stdout = StringIO()
            with patch.dict(os.environ, {"PYTHONPATH": pythonpath}), redirect_stdout(
                proofcheck_stdout
            ):
                proofcheck_code = main(["proofcheck", str(result["base_dir"])])
            proofcheck = json.loads(proofcheck_stdout.getvalue())
            self.assertNotEqual(proofcheck_code, 0)
            self.assertNotEqual(proofcheck["decision"], "pass")

        exit_events = [
            event
            for event in result["runlog"]
            if event["event"] == "exit"
            and event["payload"].get("lane_id") == "timeout-lane"
        ]
        self.assertEqual(len(exit_events), 1)
        self.assertNotEqual(exit_events[0]["payload"]["exit_code"], 0)

    def test_committed_child_exit_124_remains_a_generic_lane_failure(self):
        with tempfile.TemporaryDirectory() as bindir:
            result = self._run(
                [
                    {
                        "lane_id": "exit-124-lane",
                        "adapter": "codex",
                        "tier": "agentic",
                        "region": ["pkg/exit_124.py"],
                        "prompt": "write then return 124",
                        "codex_binary": _fake_codex_exit_124(bindir),
                    }
                ]
            )

        lane = result["ledger"]["lanes"][0]
        self.assertEqual(lane["verification_state"], "blocked")
        self.assertEqual(lane["blocked_reason"], "ERR_TEAM_LANE_FAILED")
        receipt = json.loads(
            (result["base_dir"] / "exit-124-lane" / "runner-receipt.json").read_text()
        )
        self.assertEqual(receipt["exit_code"], 124)
        self.assertIs(receipt["timed_out"], False)

    def test_zero_event_empty_diff_adapter_lane_blocks_and_proofcheck_refuses(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
        ):
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            observer_dir = out_dir / "observer"
            keys = root / "keys"
            repo.mkdir()
            out_dir.mkdir()
            observer_dir.mkdir()
            keys.mkdir()
            base_commit = _seed_repo(repo)
            private_key_path, public_key_path = gen_operator_keypair(str(keys))
            with patch("witnessd.adapter_run.probe_adapter_capability"):
                lane = _run_adapter_lane(
                    lane_id="noop-lane",
                    spec={
                        "adapter": "opencode",
                        "tier": "agentic",
                        "prompt": "edit pkg/noop.py",
                        "opencode_binary": _fake_opencode_noop(bindir),
                    },
                    repo_root=repo,
                    base_commit=base_commit,
                    base_dir=out_dir,
                    observer_dir=observer_dir,
                    allowed_touched_files=["pkg/noop.py"],
                    private_key_path=private_key_path,
                    public_key_path=public_key_path,
                    log=EventLog(str(out_dir / "runlog.jsonl")),
                    run_id="m17-noop",
                    state_root=str(root / "state"),
                )

            ledger_lane = lane["ledger_lane"]
            self.assertEqual(ledger_lane["verification_state"], "blocked")
            self.assertEqual(
                ledger_lane["blocked_reason"],
                "ERR_TEAM_LANE_ZERO_OBSERVABLE_WORK",
            )
            self.assertEqual(ledger_lane["touched_files"], [])
            self.assertEqual(lane["adapter_result"]["normalized_events"], [])
            self.assertNotIn("evidence_next_verdict", ledger_lane)
            self.assertFalse(
                (out_dir / "noop-lane" / "evidence-next-verdict.json").exists()
            )

            ledger = build_team_ledger(
                leader_objective="M17 no-op canary",
                leader_id="leader-fixed",
                start_commit=base_commit,
                stop_rule=DEFAULT_STOP_RULE,
                lanes=[ledger_lane],
            )
            (out_dir / "team-ledger.json").write_text(
                json.dumps(ledger), encoding="utf-8"
            )
            depone_root = os.environ.get("WITNESSD_DEPONE_ROOT")
            if depone_root:
                pythonpath = os.pathsep.join(
                    part
                    for part in (depone_root, os.environ.get("PYTHONPATH"))
                    if part
                )
                proofcheck_stdout = StringIO()
                with patch.dict(
                    os.environ, {"PYTHONPATH": pythonpath}
                ), redirect_stdout(proofcheck_stdout):
                    proofcheck_code = main(["proofcheck", str(out_dir)])
                proofcheck = json.loads(proofcheck_stdout.getvalue())
                self.assertNotEqual(proofcheck_code, 0)
                self.assertNotEqual(proofcheck["decision"], "pass")

                handoff_stdout = StringIO()
                with redirect_stdout(handoff_stdout):
                    handoff_code = main(
                        [
                            "orro",
                            "handoff",
                            str(out_dir),
                            "--out",
                            str(out_dir / "orro-handoff.json"),
                        ]
                    )
                self.assertNotEqual(handoff_code, 0)
                self.assertFalse((out_dir / "orro-handoff.json").exists())


def _fake_codex(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "mkdir -p pkg\n"
        "echo agent > pkg/agent.py\n"
        "saw_json=0\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--json" ]; then saw_json=1; fi\n'
        "  shift\n"
        "done\n"
        'if [ "$saw_json" -ne 1 ]; then echo "missing --json" >&2; exit 9; fi\n'
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_opencode_noop(directory: str) -> str:
    path = Path(directory) / "opencode"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_timeout(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "cat >/dev/null\n"
        "mkdir -p pkg\n"
        "echo committed-before-timeout > pkg/timeout.py\n"
        "git add pkg/timeout.py\n"
        "git commit -qm committed-before-timeout\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T-timeout"}\'\n'
        "sleep 5\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_exit_124(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "cat >/dev/null\n"
        "mkdir -p pkg\n"
        "echo child-exit-124 > pkg/exit_124.py\n"
        "git add pkg/exit_124.py\n"
        "git commit -qm child-exit-124\n"
        "exit 124\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_frontend(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "mkdir -p frontend/src\n"
        "echo app > frontend/src/App.tsx\n"
        "saw_json=0\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--json" ]; then saw_json=1; fi\n'
        "  shift\n"
        "done\n"
        'if [ "$saw_json" -ne 1 ]; then echo "missing --json" >&2; exit 9; fi\n'
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T-frontend"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def _fake_codex_touches_outside_region(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "mkdir -p pkg\n"
        "echo outside > pkg/outside.py\n"
        "saw_json=0\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--json" ]; then saw_json=1; fi\n'
        "  shift\n"
        "done\n"
        'if [ "$saw_json" -ne 1 ]; then echo "missing --json" >&2; exit 9; fi\n'
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
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

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
        ):
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
                        "commands": [
                            ["sh", "-c", "mkdir -p pkg && echo shell > pkg/shell.py"]
                        ],
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

            verdict = build_team_ledger_verdict(
                result["ledger"], base_dir=result["base_dir"]
            )
            self.assertEqual(verdict["decision"], "pass")
            kinds = {
                lane["lane_id"]: lane["runner_adapter_kind"]
                for lane in result["ledger"]["lanes"]
            }
            self.assertEqual(kinds, {"shell-lane": "shell", "codex-lane": "codex"})
            receipt = json.loads(
                (result["base_dir"] / "codex-lane" / "runner-receipt.json").read_text()
            )
            self.assertEqual(validate_runner_receipt(receipt), [])
            self.assertEqual(receipt["runner_kind"], "codex-cli")
            self.assertIn("--json", receipt["invocation"])
            self.assertNotIn("--output-last-message", receipt["invocation"])

    def test_team_run_wires_explicit_model_into_adapter_invocation(self):
        # run_team's lane specs are the same dicts _role_lane_plan_team_specs
        # (and _parse_team_lane) build -- if either one sets spec["model"] but
        # fanin's per-lane executor never reads it, the model routing policy
        # would silently do nothing once it reaches a real team run. This
        # closes that specific link (fake codex binary is enough here since
        # this only checks the invocation shape, not real model acceptance --
        # that's already live-verified in test_codex_live_smoke.py).
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
        ):
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
                        "lane_id": "codex-lane",
                        "adapter": "codex",
                        "tier": "frontier",
                        "model": "gpt-5.5",
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

            receipt = json.loads(
                (result["base_dir"] / "codex-lane" / "runner-receipt.json").read_text()
            )
            self.assertIn("-m", receipt["invocation"])
            self.assertIn("gpt-5.5", receipt["invocation"])

    def test_cli_adapter_lane_region_bounds_capture_manifest(self):
        from depone.agent_fabric.capture_bridge import validate_capture_manifest

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
        ):
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
            lane["capture_profile"] = "full"
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
