import json
import unittest

from witnessd.skill_observation import observed_skills_from_raw_events


class SkillObservationTests(unittest.TestCase):
    def test_codex_jsonl_extracts_skill_name_from_skill_file_read(self) -> None:
        raw = b"\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "sed -n '1,80p' .agents/skills/tikz-refine/SKILL.md",
                        },
                    }
                ).encode("utf-8"),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "message",
                            "text": "using the `figure-agent` skill for this lane",
                        },
                    }
                ).encode("utf-8"),
            ]
        )

        self.assertEqual(
            observed_skills_from_raw_events(raw, adapter="codex"),
            ["figure-agent", "tikz-refine"],
        )

    def test_codex_jsonl_without_skill_access_returns_empty_list(self) -> None:
        raw = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "text": "no skill access here"},
            }
        ).encode("utf-8")

        self.assertEqual(observed_skills_from_raw_events(raw, adapter="codex"), [])

    def test_non_codex_adapter_returns_empty_list(self) -> None:
        self.assertEqual(
            observed_skills_from_raw_events(
                b'{"type":"item.completed","item":{"text":"/skills/tikz-refine/SKILL.md"}}',
                adapter="claude",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
