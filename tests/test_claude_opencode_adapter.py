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


if __name__ == "__main__":
    unittest.main()
