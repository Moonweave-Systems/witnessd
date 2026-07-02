import io
import json
import unittest
import shutil
import stat
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import _parse_team_lane, main
from witnessd.canonical import canonical_hash
from witnessd.planner import (
    PlannerError,
    dispatch,
    lane_packet_from_team_lane,
    lane_packet_to_team_lane,
    plan_heuristic,
    seal_plan,
)


class TestLanePacket(unittest.TestCase):
    def test_lane_packet_round_trips_through_w7_team_lane_parser(self):
        packet = {
            "lane_id": "L1",
            "adapter": "codex",
            "tier": "agentic",
            "region": ["pkg/a.py", "pkg/b.py"],
            "prompt": "implement planner",
            "budget": {"max_tokens": 1000, "max_usd": 0.0, "max_depth": 2},
            "stop_rule": "evidence-pending",
        }

        team_lane = lane_packet_to_team_lane(packet)
        reparsed = _parse_team_lane(team_lane)
        round_tripped = lane_packet_from_team_lane(
            reparsed,
            budget=packet["budget"],
            stop_rule=packet["stop_rule"],
        )

        self.assertEqual(round_tripped, packet)

    def test_lane_packet_requires_explicit_adapter(self):
        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_PACKET_ADAPTER"):
            lane_packet_to_team_lane(
                {
                    "lane_id": "L1",
                    "tier": "agentic",
                    "region": ["pkg/a.py"],
                    "prompt": "missing adapter",
                    "budget": {"max_tokens": 1000, "max_usd": 0.0, "max_depth": 2},
                    "stop_rule": "evidence-pending",
                }
            )

    def test_lane_packet_rejects_adapter_not_supported_by_w7_parser(self):
        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_PACKET_ADAPTER"):
            lane_packet_to_team_lane(
                {
                    "lane_id": "L1",
                    "adapter": "frobnicate",
                    "tier": "agentic",
                    "region": ["pkg/a.py"],
                    "prompt": "bad adapter",
                    "budget": {"max_tokens": 1000, "max_usd": 0.0, "max_depth": 2},
                    "stop_rule": "evidence-pending",
                }
            )


class TestSealPlan(unittest.TestCase):
    def _packet(self, lane_id: str, region: list[str]) -> dict:
        return {
            "lane_id": lane_id,
            "adapter": "shell",
            "tier": "quick",
            "region": region,
            "prompt": f"write {lane_id}",
            "budget": {"max_tokens": 1000, "max_usd": 0.0, "max_depth": 1},
            "stop_rule": "evidence-pending",
        }

    def test_seal_plan_hashes_packet_list_only(self):
        packets = [self._packet("L1", ["pkg/a.py"])]

        sealed = seal_plan(packets, goal="ship W11")

        self.assertEqual(sealed["kind"], "witnessd-sealed-plan")
        self.assertEqual(sealed["schema_version"], "1.0")
        self.assertEqual(sealed["goal"], "ship W11")
        self.assertEqual(sealed["packets"], packets)
        self.assertEqual(sealed["plan_hash"], canonical_hash(packets))

    def test_seal_plan_rejects_implicit_region_overlap(self):
        packets = [
            self._packet("L1", ["pkg/shared.py"]),
            self._packet("L2", ["pkg/shared.py"]),
        ]

        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_REGION_OVERLAP"):
            seal_plan(packets, goal="ship W11")

    def test_seal_plan_rejects_normalized_region_overlap(self):
        packets = [
            self._packet("L1", ["pkg/shared.py"]),
            self._packet("L2", ["./pkg/shared.py"]),
        ]

        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_REGION_OVERLAP"):
            seal_plan(packets, goal="ship W11")

    def test_unrelated_merge_lane_does_not_bypass_region_overlap(self):
        merge = self._packet("merge", ["pkg/merge.py"])
        merge["merge_lane"] = True
        packets = [
            self._packet("L1", ["pkg/shared.py"]),
            self._packet("L2", ["pkg/shared.py"]),
            merge,
        ]

        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_REGION_OVERLAP"):
            seal_plan(packets, goal="ship W11")

    def test_merge_lane_may_overlap_region_it_merges(self):
        merge = self._packet("merge", ["pkg/shared.py"])
        merge["merge_lane"] = True

        sealed = seal_plan(
            [self._packet("L1", ["pkg/shared.py"]), merge],
            goal="ship W11",
        )

        self.assertEqual(sealed["packets"][1]["merge_lane"], True)


class TestHeuristicPlanner(unittest.TestCase):
    def test_same_goal_seed_and_root_produce_identical_packet_hash(self):
        packets_a = plan_heuristic("ship W11 planner", seed="w11", root=".")
        packets_b = plan_heuristic("ship W11 planner", seed="w11", root=".")

        self.assertEqual(canonical_hash(packets_a), canonical_hash(packets_b))
        self.assertEqual(packets_a, packets_b)
        self.assertEqual(packets_a[0]["adapter"], "shell")
        self.assertEqual(packets_a[0]["stop_rule"], "evidence-pending")

    def test_missing_root_is_explicit_error(self):
        with self.assertRaisesRegex(PlannerError, "ERR_PLAN_ROOT_MISSING"):
            plan_heuristic("ship W11 planner", seed="w11", root="/definitely/missing")


class TestDispatch(unittest.TestCase):
    def test_dispatch_is_pure_for_same_sealed_plan(self):
        packets = plan_heuristic("ship W11 planner", seed="w11", root=".")
        sealed = seal_plan(packets, goal="ship W11 planner")

        events_a = dispatch(sealed)
        events_b = dispatch(sealed)

        self.assertEqual(events_a, events_b)
        self.assertEqual(events_a[0]["kind"], "witnessd-dispatch-event")
        self.assertEqual(events_a[0]["plan_hash"], sealed["plan_hash"])
        self.assertIn("idempotency_key", events_a[0])


def _fake_codex_invalid_draft(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "printf '%s\\n' 'not json' > \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestPlannerCliDraft(unittest.TestCase):
    def test_draft_adapter_parse_failure_falls_back_to_sealed_heuristic_plan(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            repo = Path(root) / "repo"
            draft_dir = Path(root) / "draft"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "plan",
                        "ship W11 planner",
                        "--root",
                        str(repo),
                        "--draft-adapter",
                        "codex",
                        "--draft-out",
                        str(draft_dir / "evidence"),
                        "--codex-binary",
                        _fake_codex_invalid_draft(Path(bindir)),
                    ]
                )

            self.assertEqual(code, 0)
            rendered = json.loads(stdout.getvalue())
            self.assertEqual(rendered["sealed_plan"]["kind"], "witnessd-sealed-plan")
            self.assertEqual(
                rendered["sealed_plan"]["plan_hash"],
                canonical_hash(rendered["sealed_plan"]["packets"]),
            )
            self.assertEqual(rendered["draft_events"][0]["status"], "fallback")
            self.assertEqual(rendered["draft_events"][0]["reason"], "ERR_PLAN_DRAFT_PARSE")
            self.assertTrue((draft_dir / "adapter-command.json").exists())


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestPlannerPlanRunCli(unittest.TestCase):
    def test_team_plan_run_uses_heuristic_shell_lane_and_prints_pending_status(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            out_dir = Path(root) / "out"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "w11"], cwd=repo, check=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "team",
                        "plan-run",
                        "smoke goal",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            self.assertTrue((out_dir / "sealed-plan.json").exists())
            self.assertTrue((out_dir / "dispatch-log.jsonl").exists())
            ledger = json.loads((out_dir / "team-ledger.json").read_text())
            self.assertEqual(ledger["lanes"][0]["runner_adapter_kind"], "shell")

    def test_team_plan_run_can_repeat_same_goal(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "w11"], cwd=repo, check=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

            for out_name in ("out-a", "out-b"):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(
                        [
                            "team",
                            "plan-run",
                            "repeatable goal",
                            "--repo",
                            str(repo),
                            "--out",
                            str(Path(root) / out_name),
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertIn("evidence-pending", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
