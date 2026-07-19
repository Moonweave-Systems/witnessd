from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.cli._output import _emit_orro_error
from witnessd.distribution import (
    ERR_WITNESSD_DEPONE_PROVISION_FAILED,
    ProvisionError,
)


class StructuredErrorRenderingTests(unittest.TestCase):
    def test_human_mode_renders_all_structured_error_fields(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            _emit_orro_error(
                argparse.Namespace(json=False),
                code="ERR_EXAMPLE",
                message="example message",
                reason="example reason",
                required_input_or_grant="example input",
                next_command="example --command",
            )

        self.assertEqual(
            stderr.getvalue().splitlines(),
            [
                "ERR_EXAMPLE",
                "message: example message",
                "reason: example reason",
                "required_input_or_grant: example input",
                "next_command: example --command",
            ],
        )

    def test_json_mode_remains_byte_identical(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            _emit_orro_error(
                argparse.Namespace(json=True),
                code="ERR_EXAMPLE",
                message="example message",
                reason="example reason",
                required_input_or_grant="example input",
                next_command="example --command",
            )

        self.assertEqual(
            stdout.getvalue(),
            '{"error": {"code": "ERR_EXAMPLE", "message": "example message", '
            '"next_command": "example --command", "reason": "example reason", '
            '"required_input_or_grant": "example input"}}\n',
        )

    def test_orro_setup_provision_failure_has_human_remediation(self) -> None:
        stderr = io.StringIO()

        with (
            patch(
                "witnessd.distribution.init_witnessd_home",
                side_effect=ProvisionError(ERR_WITNESSD_DEPONE_PROVISION_FAILED),
            ),
            redirect_stderr(stderr),
        ):
            code = main(["orro", "setup", "--home", "/tmp/orro-home"])

        self.assertEqual(code, 2)
        text = stderr.getvalue()
        self.assertIn(ERR_WITNESSD_DEPONE_PROVISION_FAILED, text)
        self.assertIn("reason:", text)
        self.assertIn("required_input_or_grant:", text)
        self.assertIn("next_command:", text)

    def test_verify_missing_input_has_human_remediation(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = main(["verify"])

        self.assertEqual(code, 2)
        text = stderr.getvalue()
        self.assertIn("ERR_VERIFY_RUN_DIR_OR_RUNLOG_REQUIRED", text)
        self.assertIn("reason:", text)
        self.assertIn("required_input_or_grant:", text)
        self.assertIn("next_command:", text)


class RepoFlagConsistencyTests(unittest.TestCase):
    def test_proofrun_goal_uses_root_when_repo_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target-repo"
            repo.mkdir()
            stdout = io.StringIO()

            with (
                patch("witnessd.distribution.validate_depone_pin") as validate,
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "make a change",
                        "--root",
                        str(repo),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            validate.assert_called_once_with((repo / ".witnessd").resolve(strict=False))
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_PROOFRUN_NO_PLAN",
            )


if __name__ == "__main__":
    unittest.main()
