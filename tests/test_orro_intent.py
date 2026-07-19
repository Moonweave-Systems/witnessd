from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


class DeclaredIntentHelpersTest(unittest.TestCase):
    def test_documented_fixture_is_read_verbatim_and_referenced_by_file_bytes(self) -> None:
        from witnessd.orro_intent import declared_intent_ref, read_declared_intent

        fixture = Path(__file__).parent / "fixtures" / "orro-declared-intent.json"
        payload = read_declared_intent(fixture)

        self.assertEqual(
            payload,
            {
                "intent": "Preserve the reading flow while making the human decision context visible.",
                "non_goals": ["another paper-chat assistant"],
                "constraints": ["Keep execution lanes unchanged"],
            },
        )
        self.assertEqual(
            declared_intent_ref(fixture),
            {
                "path": str(fixture),
                "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "declared": True,
            },
        )

    def test_schema_rejects_empty_intent_and_non_string_list_members(self) -> None:
        from witnessd.orro_advisory import OrroAdvisoryError
        from witnessd.orro_intent import read_declared_intent

        invalid_values = [
            {"intent": ""},
            {"intent": "why", "non_goals": [1]},
            {"intent": "why", "constraints": "keep it small"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, value in enumerate(invalid_values):
                with self.subTest(value=value):
                    path = Path(tmp) / f"invalid-{index}.json"
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(OrroAdvisoryError) as raised:
                        read_declared_intent(path)
                    self.assertEqual(raised.exception.code, "ERR_ORRO_INTENT_INVALID")
                    self.assertIn(
                        "Schema: {intent: str, non_goals?: [str], constraints?: [str]}",
                        str(raised.exception),
                    )

    def test_inline_text_has_actionable_path_and_schema_error(self) -> None:
        from witnessd.orro_advisory import OrroAdvisoryError
        from witnessd.orro_intent import read_declared_intent

        with self.assertRaises(OrroAdvisoryError) as raised:
            read_declared_intent(Path("inline human intent"))

        self.assertEqual(raised.exception.code, "ERR_ORRO_INTENT_READ_FAILED")
        self.assertIn("expects a path to a JSON file, not inline text", str(raised.exception))
        self.assertIn(
            "Schema: {intent: str, non_goals?: [str], constraints?: [str]}",
            str(raised.exception),
        )

    def test_lexical_screening_is_advisory_and_deterministic(self) -> None:
        from witnessd.orro_intent import screen_intent_drift

        warnings = screen_intent_drift(
            "Build a paper-chat workflow for readers.",
            ["another paper-chat assistant", "do not add billing"],
        )

        self.assertEqual(
            warnings,
            [
                {
                    "non_goal": "another paper-chat assistant",
                    "matched_token": "paper-chat",
                    "matched_in": "Build a paper-chat workflow for readers.",
                    "method": "lexical-screening",
                    "can_change_evidence_verdict": False,
                }
            ],
        )

    def test_lexical_screening_never_raises_for_unexpected_values(self) -> None:
        from witnessd.orro_intent import screen_intent_drift

        self.assertEqual(
            screen_intent_drift(None, [None]),  # type: ignore[arg-type,list-item]
            [],
        )

    def test_all_three_public_surfaces_document_the_fixture_and_schema(self) -> None:
        for command in ("sketch", "report", "check"):
            with self.subTest(command=command):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as raised:
                        main(["orro", command, "--help"])
                self.assertEqual(raised.exception.code, 0)
                help_text = stdout.getvalue()
                self.assertIn("--intent INTENT_JSON_PATH", help_text)
                self.assertIn("intent: str", help_text)
                self.assertIn("tests/fixtures/orro-declared-intent.json", help_text)


if __name__ == "__main__":
    unittest.main()
