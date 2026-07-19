import json
import io
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from depone.agent_fabric.team_ledger import (
    build_team_ledger_verdict,
    validate_team_schedule_receipt,
)

from witnessd.eventlog import EventLog
from witnessd.fanin import _read_lane_exec_result, run_team
from witnessd.killswitch import active_targets_from_runlog
from witnessd.__main__ import main
from witnessd.cli.team_ops import _codex_specs_are_isolated
from witnessd.signing import gen_operator_keypair


_HAS_OPENSSL = shutil.which("openssl") is not None


def _seed_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w15"], cwd=repo, check=True)
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
class TestW15ParallelExecution(unittest.TestCase):
    def _run(self, lane_specs: list[dict], **kwargs):
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
            **kwargs,
        )
        result["repo"] = repo
        return result

    def test_parallel_lanes_emit_depone_schedule_receipt_with_overlap(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [
                        ["sh", "-c", "sleep 0.35; mkdir -p pkg; echo a > pkg/a.py"]
                    ],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/b.py"],
                    "commands": [
                        ["sh", "-c", "sleep 0.35; mkdir -p pkg; echo b > pkg/b.py"]
                    ],
                },
                {
                    "lane_id": "lane-c",
                    "region": ["pkg/c.py"],
                    "commands": [
                        ["sh", "-c", "sleep 0.35; mkdir -p pkg; echo c > pkg/c.py"]
                    ],
                },
            ],
            max_parallel=3,
        )

        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        self.assertEqual(ledger["schedule_receipt"], "team-schedule-receipt.json")
        receipt = json.loads(
            (result["base_dir"] / ledger["schedule_receipt"]).read_text()
        )

        self.assertEqual(validate_team_schedule_receipt(receipt), [])
        self.assertEqual(
            receipt["boundary"]["note"],
            "single-host orchestrator clock process-concurrency basis",
        )
        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])
        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(
            verdict["schedule_receipt"]["lane_ids"], ["lane-a", "lane-b", "lane-c"]
        )
        self.assertGreaterEqual(verdict["schedule_receipt"]["derived_max_overlap"], 2)
        self.assertEqual(
            {lane["touched_files"][0] for lane in ledger["lanes"]},
            {"pkg/a.py", "pkg/b.py", "pkg/c.py"},
        )

    def test_fail_fast_cancels_siblings_after_first_failed_lane_and_waits(self):
        result = self._run(
            [
                {
                    "lane_id": "fail-lane",
                    "region": ["pkg/fail.py"],
                    "commands": [["sh", "-c", "exit 7"]],
                },
                {
                    "lane_id": "slow-a",
                    "region": ["pkg/slow_a.py"],
                    "commands": [
                        ["sh", "-c", "sleep 5; mkdir -p pkg; echo a > pkg/slow_a.py"]
                    ],
                },
                {
                    "lane_id": "slow-b",
                    "region": ["pkg/slow_b.py"],
                    "commands": [
                        ["sh", "-c", "sleep 5; mkdir -p pkg; echo b > pkg/slow_b.py"]
                    ],
                },
            ],
            max_parallel=3,
            fail_fast=True,
        )

        ledger = json.loads((result["base_dir"] / "team-ledger.json").read_text())
        lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
        self.assertEqual(lanes["fail-lane"]["verification_state"], "blocked")
        self.assertEqual(lanes["fail-lane"]["blocked_reason"], "ERR_TEAM_LANE_FAILED")
        for lane_id in ("slow-a", "slow-b"):
            self.assertEqual(lanes[lane_id]["verification_state"], "blocked")
            self.assertEqual(
                lanes[lane_id]["blocked_reason"],
                "ERR_TEAM_LANE_CANCELLED_FAIL_FAST",
            )

        exit_events = [event for event in result["runlog"] if event["event"] == "exit"]
        self.assertEqual(
            {event["payload"]["lane_id"] for event in exit_events},
            {"fail-lane", "slow-a", "slow-b"},
        )
        self.assertEqual(len(result["supervisor_handles"]), 0)

    def test_fail_fast_kills_lane_grandchild_processes(self):
        marker = f"W15_ORPHAN_CHECK_{os.getpid()}"
        result = self._run(
            [
                {
                    "lane_id": "fail-lane",
                    "region": ["pkg/fail.py"],
                    "commands": [["sh", "-c", "exit 7"]],
                },
                {
                    "lane_id": "slow-lane",
                    "region": ["pkg/slow.py"],
                    "commands": [["sh", "-c", f"sleep 30 # {marker}"]],
                },
            ],
            max_parallel=2,
            fail_fast=True,
        )

        self.assertEqual(len(result["supervisor_handles"]), 0)
        self.addCleanup(_kill_marker_processes, marker)
        self.assertEqual(_marker_pids(marker), [])

    def test_normal_completion_kills_lane_grandchild_processes(self):
        marker = f"W15_COMPLETION_ORPHAN_CHECK_{os.getpid()}"
        self.addCleanup(_kill_marker_processes, marker)
        result = self._run(
            [
                {
                    "lane_id": "complete-lane",
                    "region": ["pkg/complete.py"],
                    "commands": [
                        [
                            "sh",
                            "-c",
                            f"/usr/bin/python3 -c 'import time; time.sleep(30)' {marker} "
                            "</dev/null >/dev/null 2>&1 & exit 0",
                        ]
                    ],
                }
            ],
            max_parallel=1,
        )

        self.assertEqual(_marker_pids(marker), [])
        self.assertEqual(len(result["supervisor_handles"]), 0)

    def test_parent_refuses_stale_lane_exec_result_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "lane-result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "attempt_id": "old-attempt",
                        "lane": {
                            "ledger_lane": {
                                "lane_id": "lane-a",
                                "verification_state": "pass",
                                "evidence_dir": "stale-evidence",
                            }
                        },
                        "lane_exec_pid": 123,
                        "lane_exec_pid_start_token": "123:old",
                        "lane_id": "lane-a",
                        "run_id": "team-run",
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "attempt_id": "fresh-attempt",
                "lane_exec_pid": 456,
                "lane_exec_pid_start_token": "456:fresh",
                "lane_id": "lane-a",
                "result_path": result_path,
                "run_id": "team-run",
                "spec": {"adapter": "shell"},
            }

            lane = _read_lane_exec_result(job, "base-commit", -9)

            self.assertEqual(lane["ledger_lane"]["verification_state"], "blocked")
            self.assertEqual(
                lane["ledger_lane"]["blocked_reason"], "ERR_TEAM_LANE_EXEC_FAILED"
            )
            self.assertNotEqual(
                lane["ledger_lane"].get("evidence_dir"), "stale-evidence"
            )

    def test_forged_schedule_receipt_interval_is_rejected_by_depone(self):
        result = self._run(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg; echo a > pkg/a.py"]],
                }
            ],
            max_parallel=1,
        )
        ledger_path = result["base_dir"] / "team-ledger.json"
        ledger = json.loads(ledger_path.read_text())
        receipt_path = result["base_dir"] / ledger["schedule_receipt"]
        receipt = json.loads(receipt_path.read_text())
        receipt["lanes"][0]["exited_monotonic_ns"] = (
            receipt["lanes"][0]["spawned_monotonic_ns"] - 1
        )
        receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")

        verdict = build_team_ledger_verdict(ledger, base_dir=result["base_dir"])

        self.assertEqual(verdict["decision"], "blocked")
        self.assertIn(
            "ERR_TEAM_SCHEDULE_RECEIPT_INTERVAL_INVALID",
            {error["code"] for error in verdict["errors"]},
        )

    def test_team_kill_state_root_uses_recorded_team_runlog_and_kills_tree(self):
        marker = f"W15_TEAM_KILL_{os.getpid()}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            out_dir = root / "evidence"
            state_root = root / "state"
            keys = root / "keys"
            repo.mkdir()
            keys.mkdir()
            _seed_repo(repo)
            slow_command = f'trap "" TERM; while true; do sleep 1; done # {marker}'
            launch_code = (
                "from witnessd.fanin import run_team\n"
                "from witnessd.signing import gen_operator_keypair\n"
                "import sys\n"
                "priv,pub=gen_operator_keypair(sys.argv[3])\n"
                "run_team([\n"
                "  {\n"
                "    'lane_id':'slow-lane',\n"
                "    'region':['pkg/slow.py'],\n"
                f"    'commands':[['sh','-c',{slow_command!r}]],\n"
                "  }\n"
                "], repo_root=sys.argv[1], out_dir=sys.argv[2],\n"
                "   private_key_path=priv, public_key_path=pub,\n"
                "   state_root=sys.argv[4], max_parallel=1, run_id='team-run')\n"
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    launch_code,
                    str(repo),
                    str(out_dir),
                    str(keys),
                    str(state_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                _wait_for(
                    lambda: (
                        (state_root / "team-run.json").is_file()
                        and bool(_marker_pids(marker))
                        and _team_run_has_active_target(state_root)
                    ),
                    timeout=15,
                )
                self.assertEqual(
                    main(["team", "kill", "--state-root", str(state_root)]), 0
                )
                proc.wait(timeout=5)
                self.addCleanup(_kill_marker_processes, marker)
                self.assertEqual(_marker_pids(marker), [])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                _kill_marker_processes(marker)

    def test_team_kill_state_root_requires_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "state"
            state_root.mkdir()
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(["team", "kill", "--state-root", str(state_root)])

            self.assertEqual(code, 2)
            self.assertIn("ERR_TEAM_KILL_STATE_MANIFEST_MISSING", stderr.getvalue())

    def test_resume_audit_classifies_missing_lane_result_as_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            control = out_dir / ".lane-exec"
            control.mkdir(parents=True)
            (control / "done.json").write_text(
                json.dumps({"lane_id": "done", "spec": {}, "run_id": "team-run"}),
                encoding="utf-8",
            )
            (control / "done-result.json").write_text(
                json.dumps(
                    {
                        "run_id": "team-run",
                        "lane": {
                            "ledger_lane": {
                                "lane_id": "done",
                                "verification_state": "pass",
                                "evidence_dir": "done",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (control / "lost.json").write_text(
                json.dumps({"lane_id": "lost", "spec": {}, "run_id": "team-run"}),
                encoding="utf-8",
            )

            code = main(
                ["team", "resume-audit", "--out", str(out_dir), "--run-id", "team-run"]
            )

            self.assertEqual(code, 0)
            audit = json.loads((out_dir / "team-resume-audit.json").read_text())
            lanes = {lane["lane_id"]: lane for lane in audit["lanes"]}
            self.assertEqual(lanes["done"]["classification"], "complete")
            self.assertEqual(lanes["lost"]["classification"], "indeterminate")
            self.assertEqual(
                lanes["lost"]["blocked_reason"],
                "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH",
            )
            self.assertFalse(audit["boundary"]["replay_resume"])
            self.assertFalse(audit["boundary"]["fabricates_completion"])

    def test_resume_audit_refuses_stale_result_from_different_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            control = out_dir / ".lane-exec"
            control.mkdir(parents=True)
            (control / "lane-a.json").write_text(
                json.dumps({"lane_id": "lane-a", "spec": {}, "run_id": "new-run"}),
                encoding="utf-8",
            )
            (control / "lane-a-result.json").write_text(
                json.dumps(
                    {
                        "run_id": "old-run",
                        "lane": {"ledger_lane": {"verification_state": "pass"}},
                    }
                ),
                encoding="utf-8",
            )

            code = main(
                ["team", "resume-audit", "--out", str(out_dir), "--run-id", "new-run"]
            )

            self.assertEqual(code, 0)
            audit = json.loads((out_dir / "team-resume-audit.json").read_text())
            self.assertEqual(audit["lanes"][0]["classification"], "indeterminate")
            self.assertEqual(
                audit["lanes"][0]["blocked_reason"],
                "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH",
            )

    def test_resume_audit_refuses_truncated_matching_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            control = out_dir / ".lane-exec"
            control.mkdir(parents=True)
            (control / "lane-a.json").write_text(
                json.dumps({"lane_id": "lane-a", "spec": {}, "run_id": "team-run"}),
                encoding="utf-8",
            )
            (control / "lane-a-result.json").write_text(
                json.dumps({"run_id": "team-run"}),
                encoding="utf-8",
            )

            code = main(
                ["team", "resume-audit", "--out", str(out_dir), "--run-id", "team-run"]
            )

            self.assertEqual(code, 0)
            audit = json.loads((out_dir / "team-resume-audit.json").read_text())
            self.assertEqual(audit["lanes"][0]["classification"], "indeterminate")
            self.assertEqual(
                audit["lanes"][0]["blocked_reason"],
                "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH",
            )

    def test_resume_audit_refuses_malformed_truncated_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            control = out_dir / ".lane-exec"
            control.mkdir(parents=True)
            (control / "lane-a.json").write_text(
                json.dumps({"lane_id": "lane-a", "spec": {}, "run_id": "team-run"}),
                encoding="utf-8",
            )
            (control / "lane-a-result.json").write_text(
                '{"run_id":"team-run","lane":',
                encoding="utf-8",
            )

            code = main(
                ["team", "resume-audit", "--out", str(out_dir), "--run-id", "team-run"]
            )

            self.assertEqual(code, 0)
            audit = json.loads((out_dir / "team-resume-audit.json").read_text())
            self.assertEqual(audit["lanes"][0]["classification"], "indeterminate")
            self.assertEqual(
                audit["lanes"][0]["blocked_reason"],
                "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH",
            )

    def test_resume_audit_refuses_malformed_spec_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            control = out_dir / ".lane-exec"
            control.mkdir(parents=True)
            (control / "lane-a.json").write_text(
                '{"lane_id":"lane-a","run_id":',
                encoding="utf-8",
            )

            code = main(
                ["team", "resume-audit", "--out", str(out_dir), "--run-id", "team-run"]
            )

            self.assertEqual(code, 0)
            audit = json.loads((out_dir / "team-resume-audit.json").read_text())
            self.assertEqual(audit["lanes"][0]["lane_id"], "lane-a")
            self.assertEqual(audit["lanes"][0]["classification"], "indeterminate")
            self.assertEqual(
                audit["lanes"][0]["blocked_reason"],
                "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH",
            )

    def test_multi_codex_guard_allows_distinct_isolated_state_roots_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(
                _codex_specs_are_isolated(
                    [
                        {"state_root": str(root / "lane-a")},
                        {"state_root": str(root / "lane-b")},
                    ]
                )
            )
            self.assertFalse(
                _codex_specs_are_isolated(
                    [
                        {"state_root": str(root / "shared")},
                        {"state_root": str(root / "shared")},
                    ]
                )
            )
            self.assertFalse(
                _codex_specs_are_isolated(
                    [
                        {"state_root": str(root / "parent")},
                        {"state_root": str(root / "parent" / "child")},
                    ]
                )
            )


def _marker_pids(marker: str) -> list[int]:
    completed = subprocess.run(
        ["pgrep", "-f", marker],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = []
    for line in completed.stdout.splitlines():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return pids


def _kill_marker_processes(marker: str) -> None:
    for pid in _marker_pids(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _team_run_has_active_target(state_root: Path) -> bool:
    # The real race this closes: supervisor.spawn() forks the lane process
    # (visible via pgrep almost immediately) and only appends its "spawn"
    # runlog event afterward. `team kill` takes a one-shot snapshot read of
    # that runlog, so if it runs in the gap before the spawn event is
    # durably written, active_targets_from_runlog() sees nothing and kill_all
    # returns ERR_WITNESSD_KILL_NO_TARGETS -- process visibility via pgrep is
    # not sufficient proof that kill has something to act on. Wait on the
    # exact condition team-run.json/kill itself will check, not a proxy for it.
    manifest_path = state_root / "team-run.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    runlog_path = manifest.get("runlog")
    if not isinstance(runlog_path, str) or not os.path.isfile(runlog_path):
        return False
    records = EventLog(runlog_path).read()
    return bool(active_targets_from_runlog(records))


def _wait_for(predicate, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition did not become true")


if __name__ == "__main__":
    unittest.main()
