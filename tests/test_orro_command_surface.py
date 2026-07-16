import contextlib
import io
import unittest

from orro.__main__ import main as orro_main
from witnessd.__main__ import ORRO_COMMAND_MAP, _normalize_orro_argv


class OrroCommandSurfaceTests(unittest.TestCase):
    def test_unknown_command_names_token_and_valid_commands(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = orro_main(["workflow", "--help"])

        self.assertEqual(result, 2)
        message = stderr.getvalue()
        self.assertIn("orro: unknown command 'workflow'", message)
        self.assertIn("flowplan", message)
        self.assertNotIn("invalid choice: 'orro'", message)

    def test_unknown_command_suggests_close_match(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = orro_main(["flowpln"])

        self.assertEqual(result, 2)
        self.assertIn("orro: unknown command 'flowpln'", stderr.getvalue())
        self.assertIn("did you mean 'flowplan'?", stderr.getvalue())

    def test_recognized_commands_keep_existing_normalization(self) -> None:
        expected = {
            "setup": "orro-setup",
            "init": "init",
            "scout": "scout",
            "flowplan": "flowplan",
            "proofrun": "proofrun",
            "proofcheck": "proofcheck",
            "advisory-provenance-check": "advisory-provenance-check",
            "handoff": "handoff",
            "doctor": "orro-doctor",
            "engine-lock": "engine-lock",
            "lock": "engine-lock",
            "next": "orro-next",
            "advise": "orro-advise",
            "sketch": "orro-sketch",
            "trace": "orro-trace",
            "report": "orro-report",
            "review": "orro-review",
            "auto": "orro-auto",
            "team": "team",
        }

        self.assertEqual(ORRO_COMMAND_MAP, expected)
        for public_command, witnessd_command in expected.items():
            self.assertEqual(
                _normalize_orro_argv(["orro", public_command]),
                [witnessd_command],
            )


if __name__ == "__main__":
    unittest.main()
