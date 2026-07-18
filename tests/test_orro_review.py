from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "orro-review@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Review"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("# review fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _fake_agy(directory: Path) -> str:
    path = directory / "agy"
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "capture = os.environ.get('AGY_ARGV_CAPTURE')\n"
        "if capture:\n"
        "    pathlib.Path(capture).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n"
        "cache_capture = os.environ.get('ORRO_CACHE_CAPTURE')\n"
        "if cache_capture:\n"
        "    pathlib.Path(cache_capture).write_text(os.environ['PYTHONPYCACHEPREFIX'] + '\\n' + os.environ['RUFF_CACHE_DIR'] + '\\n', encoding='utf-8')\n"
        "if os.environ.get('AGY_WRITE_CACHE') == '1':\n"
        "    pathlib.Path(os.environ['RUFF_CACHE_DIR']).mkdir(parents=True, exist_ok=True)\n"
        "    pathlib.Path(os.environ['RUFF_CACHE_DIR'], 'cache.bin').write_text('cache', encoding='utf-8')\n"
        "    pycache = pathlib.Path(os.environ['PYTHONPYCACHEPREFIX'], 'pkg')\n"
        "    pycache.mkdir(parents=True, exist_ok=True)\n"
        "    pathlib.Path(pycache, 'mod.pyc').write_text('bytecode', encoding='utf-8')\n"
        "if os.environ.get('AGY_WRITE') == '1':\n"
        "    pathlib.Path('reviewed.txt').write_text('changed\\n', encoding='utf-8')\n"
        "if sys.stdout.isatty():\n"
        "    observed_root = os.environ.get('AGY_OBSERVED_REPO', os.getcwd())\n"
        "    observed_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=observed_root, check=True, capture_output=True, text=True).stdout.strip()\n"
        "    print('WITNESSD_AGY_CONTEXT ' + json.dumps({'repo_root': observed_root, 'git_head': observed_head}, sort_keys=True))\n"
        "    if os.environ.get('AGY_REVIEW_MODE') == 'intent-only':\n"
        "        print('I will inspect the requested files now.')\n"
        "    else:\n"
        "        print('Review findings:')\n"
        "        print('low README.md:1 review-only smoke finding')\n"
        "    if os.environ.get('AGY_COMPLETION_MODE', 'correct') != 'missing':\n"
        "        print('WITNESSD_AGY_COMPLETE ' + json.dumps({'status': 'complete'}, sort_keys=True))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_critic(directory: Path) -> str:
    path = directory / "claude"
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import pathlib\n"
        "import shlex\n"
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "settings_path = pathlib.Path(args[args.index('--settings') + 1])\n"
        "settings = json.loads(settings_path.read_text(encoding='utf-8'))\n"
        "command = settings['hooks']['PreToolUse'][0]['hooks'][0]['command']\n"
        "denied = subprocess.run(\n"
        "    shlex.split(command),\n"
        "    input=json.dumps({'tool_name': 'Edit'}),\n"
        "    text=True,\n"
        "    capture_output=True,\n"
        "    check=False,\n"
        ")\n"
        "if not denied.stdout.strip():\n"
        "    pathlib.Path('EDITED.md').write_text('edit escaped hook\\n', encoding='utf-8')\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'no findings'}))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class OrroReviewTests(unittest.TestCase):
    def test_orro_review_redirects_adapter_cache_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = repo / ".witnessd"
            repo.mkdir()
            _seed_repo(repo)
            role_lanes_out = root / "role-lane-plan.json"
            with redirect_stdout(io.StringIO()) as flow_stdout:
                flow_code = main(
                    [
                        "orro",
                        "flowplan",
                        "review the readme",
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
                )
            self.assertEqual(flow_code, 0, flow_stdout.getvalue())

            bindir = root / "bin"
            bindir.mkdir()
            cache_capture = root / "cache-env.txt"
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "ORRO_CACHE_CAPTURE": str(cache_capture),
                        "AGY_WRITE_CACHE": "1",
                    },
                ),
                redirect_stdout(stdout),
            ):
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
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            lane = payload["lanes"][0]
            evidence_root = Path(lane["review_receipt_path"]).parent.parent
            cache_dir = (
                evidence_root
                / ".witnessd"
                / "adapter-cache"
                / lane["lane_id"]
            )
            self.assertEqual(lane["touched_files"], [])
            self.assertEqual(
                cache_capture.read_text(encoding="utf-8").splitlines(),
                [str(cache_dir / "pycache"), str(cache_dir / "ruff")],
            )
            self.assertFalse((repo / ".ruff_cache").exists())
            self.assertFalse((repo / "__pycache__").exists())
            self.assertTrue((cache_dir / "ruff" / "cache.bin").is_file())
            self.assertTrue((cache_dir / "pycache" / "pkg" / "mod.pyc").is_file())

    def test_orro_review_runs_exactly_one_dedicated_claude_critic_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            repo.mkdir()
            _seed_repo(repo)
            role_lanes_out = root / "role-lane-plan.json"
            with redirect_stdout(io.StringIO()) as flow_stdout:
                flow_code = main(
                    [
                        "orro",
                        "flowplan",
                        "criticize the readme",
                        "--root",
                        str(repo),
                        "--profile",
                        "critic-only",
                        "--role-lanes-out",
                        str(role_lanes_out),
                    ]
                )
            self.assertEqual(flow_code, 0, flow_stdout.getvalue())
            role_lanes = json.loads(role_lanes_out.read_text(encoding="utf-8"))
            self.assertEqual(len(role_lanes["lanes"]), 1)
            self.assertEqual(role_lanes["lanes"][0]["adapter"], "claude")

            bindir = root / "bin"
            bindir.mkdir()
            agy_marker = root / "agy-ran"
            fake_agy = bindir / "agy"
            fake_agy.write_text(
                f"#!/bin/sh\ntouch {agy_marker}\nexit 99\n",
                encoding="utf-8",
            )
            fake_agy.chmod(fake_agy.stat().st_mode | stat.S_IEXEC)
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
                        "--claude-binary",
                        _fake_claude_critic(bindir),
                        "--agy-binary",
                        str(fake_agy),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["workflow_profile"], "critic-only")
            self.assertEqual(len(payload["lanes"]), 1)
            lane = payload["lanes"][0]
            self.assertEqual(lane["adapter"], "claude")
            self.assertFalse((repo / "EDITED.md").exists())
            self.assertFalse(agy_marker.exists())
            self.assertFalse(lane["review_receipt"]["raises_assurance"])
            self.assertFalse(lane["review_receipt"]["verifies_evidence"])
            self.assertFalse(
                lane["review_receipt"]["can_change_evidence_verdict"]
            )
            self.assertNotIn(
                repo.resolve(), Path(lane["review_receipt_path"]).resolve().parents
            )

    def test_orro_review_keeps_adapter_evidence_outside_repo_with_internal_home(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = repo / ".witnessd"
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
            with redirect_stdout(io.StringIO()) as flow_stdout:
                flow_code = main(
                    [
                        "orro",
                        "flowplan",
                        "review the readme",
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
                )
            self.assertEqual(flow_code, 0, flow_stdout.getvalue())

            bindir = root / "bin"
            bindir.mkdir()
            argv_capture = root / "agy-argv.txt"
            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"AGY_ARGV_CAPTURE": str(argv_capture)}),
                redirect_stdout(stdout),
            ):
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
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            lane = payload["lanes"][0]
            evidence_paths = [
                Path(lane["transcript_path"]),
                Path(lane["normalized_events_path"]),
                Path(lane["review_receipt_path"]),
            ]
            evidence_dir = evidence_paths[0].parent
            evidence_paths.append(evidence_dir / "command-log.json")
            for evidence_path in evidence_paths:
                self.assertTrue(evidence_path.is_file(), evidence_path)
                self.assertNotIn(repo.resolve(), evidence_path.resolve().parents)

    def test_orro_review_runs_policy_resolved_agy_lane_without_assurance(self) -> None:
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
            with redirect_stdout(io.StringIO()) as flow_stdout:
                flow_code = main(
                    [
                        "orro",
                        "flowplan",
                        "review the readme",
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
                )
            self.assertEqual(flow_code, 0, flow_stdout.getvalue())

            role_lanes = json.loads(role_lanes_out.read_text(encoding="utf-8"))
            reviewer_lane = role_lanes["lanes"][0]
            self.assertEqual(reviewer_lane["phase"], "review")
            self.assertEqual(reviewer_lane["adapter"], "agy")
            self.assertEqual(reviewer_lane["model"], "gemini-3.5-flash")
            self.assertEqual(reviewer_lane["region"], ["."])

            bindir = root / "bin"
            bindir.mkdir()
            argv_capture = root / "agy-argv.txt"
            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"AGY_ARGV_CAPTURE": str(argv_capture)}),
                redirect_stdout(stdout),
            ):
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
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-review-summary")
            self.assertEqual(payload["can_change_evidence_verdict"], False)
            self.assertEqual(payload["raises_assurance"], False)
            self.assertEqual(payload["executes_proofrun"], False)
            self.assertEqual(payload["verifies_evidence"], False)
            self.assertEqual(payload["workflow_profile"], "review-only")
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(len(payload["lanes"]), 1)

            lane = payload["lanes"][0]
            self.assertEqual(lane["lane_id"], reviewer_lane["lane_id"])
            self.assertEqual(lane["adapter"], "agy")
            self.assertEqual(lane["model"], "gemini-3.5-flash")
            self.assertEqual(lane["touched_files"], [])
            self.assertEqual(lane["review_receipt"]["kind"], "moonweave-review-receipt")
            self.assertEqual(lane["review_receipt"]["can_change_evidence_verdict"], False)
            binding = lane["review_receipt"]["context_binding"]
            self.assertEqual(binding["status"], "bound")
            self.assertEqual(binding["canonical_repo_root"], str(repo.resolve()))
            self.assertEqual(
                binding["requested_project_identity"],
                binding["observed_project_identity"],
            )
            self.assertEqual(
                binding["requested_git_head_sha"],
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            )
            self.assertEqual(
                lane["model_declaration"]["verification_status"],
                "requested-unverified",
            )

            argv = argv_capture.read_text(encoding="utf-8")
            self.assertIn("--model\n", argv)
            self.assertIn("gemini-3.5-flash\n", argv)
            run_dir = Path(payload["run_dir"])
            self.assertTrue((run_dir / "orro-review-summary.json").is_file())
            self.assertTrue((run_dir / reviewer_lane["lane_id"] / "review-receipt.json").is_file())
            self.assertFalse((run_dir / "team-ledger.json").exists())

            incomplete_stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "AGY_ARGV_CAPTURE": str(argv_capture),
                        "AGY_COMPLETION_MODE": "missing",
                        "AGY_REVIEW_MODE": "intent-only",
                    },
                ),
                redirect_stdout(incomplete_stdout),
            ):
                incomplete_code = main(
                    [
                        "orro",
                        "review",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--role-lane-plan",
                        str(role_lanes_out),
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(incomplete_code, 1, incomplete_stdout.getvalue())
            incomplete_payload = json.loads(incomplete_stdout.getvalue())
            self.assertNotEqual(incomplete_payload["decision"], "pass")
            incomplete_lane = incomplete_payload["lanes"][0]
            self.assertEqual(incomplete_lane["decision"], "incomplete-review")
            self.assertEqual(incomplete_lane["exit_code"], 0)
            self.assertFalse(incomplete_lane["review_receipt"]["findings_usable"])
            self.assertFalse(
                incomplete_lane["review_receipt"]["usable_as_review_evidence"]
            )
            self.assertIn(
                "I will inspect",
                Path(incomplete_lane["transcript_path"]).read_text(encoding="utf-8"),
            )

            stale_repo = root / "stale-repo"
            stale_repo.mkdir()
            _seed_repo(stale_repo)
            stale_stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "AGY_ARGV_CAPTURE": str(argv_capture),
                        "AGY_OBSERVED_REPO": str(stale_repo),
                    },
                ),
                redirect_stdout(stale_stdout),
            ):
                stale_code = main(
                    [
                        "orro",
                        "review",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--role-lane-plan",
                        str(role_lanes_out),
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(stale_code, 1, stale_stdout.getvalue())
            stale_payload = json.loads(stale_stdout.getvalue())
            self.assertEqual(stale_payload["decision"], "invalid-context")
            stale_lane = stale_payload["lanes"][0]
            self.assertEqual(stale_lane["decision"], "invalid-context")
            self.assertEqual(stale_lane["test_output"]["error_code"], "ERR_AGY_INVALID_CONTEXT")
            self.assertEqual(
                stale_lane["review_receipt"]["context_binding"]["status"],
                "invalid-context",
            )
            self.assertEqual(
                stale_lane["review_receipt"]["kind"],
                "moonweave-review-context-diagnostic",
            )
            self.assertEqual(stale_lane["review_receipt"]["findings"], [])
            self.assertFalse(stale_lane["review_receipt"]["findings_usable"])
            self.assertIsNone(stale_lane["transcript_path"])
            self.assertIsNone(stale_lane["normalized_events_path"])
            self.assertNotIn("review-only smoke finding", json.dumps(stale_payload))
            stale_lane_dir = Path(stale_payload["run_dir"]) / stale_lane["lane_id"]
            self.assertEqual(
                (stale_lane_dir / "events.raw.jsonl").read_bytes(), b""
            )
            self.assertNotIn(
                "review-only smoke finding",
                (stale_lane_dir / "command-log.json").read_text(encoding="utf-8"),
            )
            self.assertFalse(
                (Path(stale_payload["run_dir"]) / "orro-handoff.json").exists()
            )

            write_stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "AGY_OBSERVED_REPO": str(stale_repo),
                        "AGY_WRITE": "1",
                    },
                ),
                redirect_stdout(write_stdout),
            ):
                write_code = main(
                    [
                        "orro",
                        "review",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--role-lane-plan",
                        str(role_lanes_out),
                        "--agy-binary",
                        _fake_agy(bindir),
                        "--json",
                    ]
                )

            self.assertEqual(write_code, 1, write_stdout.getvalue())
            write_payload = json.loads(write_stdout.getvalue())
            self.assertEqual(write_payload["decision"], "fail")
            self.assertEqual(write_payload["lanes"][0]["decision"], "fail")
            self.assertEqual(write_payload["lanes"][0]["exit_code"], 125)
            self.assertIn("reviewed.txt", write_payload["lanes"][0]["touched_files"])


if __name__ == "__main__":
    unittest.main()
