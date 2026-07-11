import pathlib
import stat
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.claude import ClaudeAdapterError, run_claude_lane
from witnessd.adapters.opencode import OpenCodeAdapterError, run_opencode_lane


def _fake_cli(directory: str, name: str) -> str:
    path = pathlib.Path(directory) / name
    path.write_text(
        "#!/bin/sh\necho ran >&2\nexit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_jsonl(directory: str) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"type":"session.started","session_id":"S1"}\'\n'
        'printf \'%s\\n\' \'{"type":"assistant.message","message_id":"M1","text":"done"}\'\n'
        'printf \'%s\\n\' \'{"type":"tool.completed","tool_name":"Bash","tool_use_id":"T1"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_model_probe(directory: str, *, reject_model: str | None = None) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/bin/sh\n"
        "model=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--model" ]; then shift; model="$1"; shift; continue; fi\n'
        "  shift\n"
        "done\n"
        f'if [ "$model" = "{reject_model}" ]; then\n'
        '  printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":true,"error":"model_not_found",'
        '"result":"model rejected"}\'\n'
        "else\n"
        '  printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":false,"result":"OK"}\'\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestClaudeOpenCodeAdapter(unittest.TestCase):
    def _check(self, res, cli_name: str, worktree: str) -> None:
        self.assertEqual(res.runner_kind, "manual")
        self.assertTrue(any(cli_name in token for token in res.invocation))
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.test_output, {"status": "not-run"})
        receipt = res.to_runner_receipt(
            arm="direct",
            task_id="t",
            worktree=worktree,
            started_at="2026-07-01T00:00:00Z",
            ended_at="2026-07-01T00:00:01Z",
        )
        self.assertEqual(validate_runner_receipt(receipt), [])
        self.assertEqual(receipt["runner_kind"], "manual")

    def test_claude(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_cli(bindir, "claude"),
                transcript_path=str(pathlib.Path(bindir) / "claude.txt"),
            )

            self._check(res, "claude", sandbox)
            self.assertIn("-p", res.invocation)
            # Live-verified: claude rejects --output-format stream-json without
            # --verbose ("Error: When using --print, --output-format=stream-json
            # requires --verbose"), and without --output-format at all it never
            # emits structured JSONL, only free text -- so normalize_claude_
            # jsonl_events has nothing to parse. Both flags are required.
            self.assertIn("--output-format", res.invocation)
            self.assertEqual(
                res.invocation[res.invocation.index("--output-format") + 1],
                "stream-json",
            )
            self.assertIn("--verbose", res.invocation)

    def test_claude_jsonl_normalizes_to_agent_event_envelope(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            transcript = pathlib.Path(bindir) / "claude.raw.jsonl"
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_jsonl(bindir),
                transcript_path=str(transcript),
            )

            self.assertEqual(
                [event["event_type"] for event in res.normalized_events],
                ["thread.started", "message.completed", "command.completed"],
            )
            self.assertEqual(
                {event["schema"] for event in res.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            self.assertEqual(
                {event["provider"] for event in res.normalized_events},
                {"claude-code"},
            )
            self.assertTrue((pathlib.Path(bindir) / "events.normalized.jsonl").exists())

    def test_opencode(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_opencode_lane(
                sandbox=sandbox,
                prompt="x",
                opencode_binary=_fake_cli(bindir, "opencode"),
                transcript_path=str(pathlib.Path(bindir) / "opencode.txt"),
            )

            self._check(res, "opencode", sandbox)
            self.assertIn("run", res.invocation)

    def test_claude_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(ClaudeAdapterError) as cm:
                run_claude_lane(
                    sandbox=sandbox,
                    prompt="x",
                    claude_binary=_fake_cli(bindir, "claude"),
                    transcript_path=str(pathlib.Path(sandbox) / "claude.txt"),
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_opencode_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(OpenCodeAdapterError) as cm:
                run_opencode_lane(
                    sandbox=sandbox,
                    prompt="x",
                    opencode_binary=_fake_cli(bindir, "opencode"),
                    transcript_path=str(pathlib.Path(sandbox) / "opencode.txt"),
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_model_passed_to_claude_argv(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(bindir),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="claude-sonnet-5",
            )

        self.assertIn("--model", res.invocation)
        self.assertEqual(
            res.invocation[res.invocation.index("--model") + 1], "claude-sonnet-5"
        )

    def test_no_model_requested_emits_no_declaration(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_cli(bindir, "claude"),
                transcript_path=str(pathlib.Path(bindir) / "claude.txt"),
            )

        self.assertNotIn("--model", res.invocation)
        self.assertIsNone(res.model_declaration)

    def test_valid_model_reports_verified(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(
                    bindir, reject_model="bad-model"
                ),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="good-model",
            )

        self.assertEqual(res.exit_code, 0)
        self.assertEqual(
            res.model_declaration,
            {
                "kind": "moonweave-model-declaration",
                "schema_version": "1.0",
                "can_change_evidence_verdict": False,
                "adapter": "claude",
                "requested_model": "good-model",
                "verification_status": "verified",
                "detail": None,
            },
        )

    def test_invalid_model_rejected_by_claude_fails_closed(self):
        # Live-verified against real claude-code 2.1.207: an invalid --model
        # value does NOT change the process exit code (it stays 0) -- the
        # rejection only shows up as is_error/error on the terminal "result"
        # event. The lane must still fail closed rather than trusting exit 0.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(
                    bindir, reject_model="bad-model"
                ),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="bad-model",
            )

        self.assertEqual(res.exit_code, 125)
        self.assertEqual(res.test_output["status"], "failed")
        self.assertIn("bad-model", res.test_output["summary"])
        self.assertEqual(res.model_declaration["verification_status"], "rejected")
        self.assertEqual(res.model_declaration["requested_model"], "bad-model")
        self.assertIsNotNone(res.model_declaration["detail"])


if __name__ == "__main__":
    unittest.main()
