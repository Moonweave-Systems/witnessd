import unittest

from witnessd.__main__ import _parse_team_lane
from witnessd.canonical import canonical_hash
from witnessd.planner import (
    PlannerError,
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


class TestHeuristicPlanner(unittest.TestCase):
    def test_same_goal_seed_and_root_produce_identical_packet_hash(self):
        packets_a = plan_heuristic("ship W11 planner", seed="w11", root=".")
        packets_b = plan_heuristic("ship W11 planner", seed="w11", root=".")

        self.assertEqual(canonical_hash(packets_a), canonical_hash(packets_b))
        self.assertEqual(packets_a, packets_b)
        self.assertEqual(packets_a[0]["adapter"], "shell")
        self.assertEqual(packets_a[0]["stop_rule"], "evidence-pending")


if __name__ == "__main__":
    unittest.main()
