import unittest

from witnessd.__main__ import _parse_team_lane


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


if __name__ == "__main__":
    unittest.main()
