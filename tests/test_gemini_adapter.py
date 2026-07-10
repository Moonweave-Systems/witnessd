import json
import pathlib
import stat
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.gemini import GeminiAdapterError, run_gemini_review_lane


def _fake_gemini(directory: str, *, writes_file: bool = False) -> str:
    path = pathlib.Path(directory) / "gemini"
    write_command = "printf 'changed\\n' > reviewed.txt\n" if writes_file else ""
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$GEMINI_ARGV_CAPTURE"\n'
        f"{write_command}"
        'printf \'%s\\n\' \'{"type":"message","content":"review start"}\'\n'
        'printf \'%s\\n\' \'{"type":"tool_call","name":"read_file","id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"result","text":"[{\\"severity\\":\\"medium\\",\\"file\\":\\"pkg/a.py\\",\\"line\\":7,\\"summary\\":\\"check edge case\\"}]"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestGeminiAdapter(unittest.TestCase):
    def test_review_lane_is_headless_and_read_only(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            argv_capture = pathlib.Path(bindir) / "argv.txt"
            res = run_gemini_review_lane(
                sandbox=sandbox,
                prompt="review only",
                gemini_binary=_fake_gemini(bindir),
                transcript_path=str(pathlib.Path(bindir) / "gemini.raw.jsonl"),
                env={"GEMINI_ARGV_CAPTURE": str(argv_capture)},
            )

            self.assertEqual(res.runner_kind, "manual")
            self.assertEqual(res.exit_code, 0)
            self.assertIn("--approval-mode", res.invocation)
            self.assertEqual(
                res.invocation[res.invocation.index("--approval-mode") + 1], "plan"
            )
            self.assertIn("--output-format", res.invocation)
            self.assertEqual(
                res.invocation[res.invocation.index("--output-format") + 1],
                "stream-json",
            )
            self.assertIn("-p", res.invocation)
            self.assertEqual(res.test_output, {"status": "not-run"})
            self.assertEqual(
                validate_runner_receipt(
                    res.to_runner_receipt(
                        arm="direct",
                        task_id="review-t",
                        worktree=sandbox,
                        started_at="2026-07-01T00:00:00Z",
                        ended_at="2026-07-01T00:00:01Z",
                    )
                ),
                [],
            )

    def test_gemini_stream_normalizes_to_agent_event_envelope(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_gemini_review_lane(
                sandbox=sandbox,
                prompt="review only",
                gemini_binary=_fake_gemini(bindir),
                transcript_path=str(pathlib.Path(bindir) / "gemini.raw.jsonl"),
                env={"GEMINI_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertEqual(
                {event["schema"] for event in res.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            self.assertEqual(
                {event["provider"] for event in res.normalized_events},
                {"google-gemini"},
            )
            self.assertEqual(
                [event["event_type"] for event in res.normalized_events],
                ["message.completed", "command.completed", "turn.completed"],
            )
            self.assertTrue((pathlib.Path(bindir) / "events.normalized.jsonl").exists())

    def test_review_receipt_is_structured_and_observed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            receipt_path = pathlib.Path(bindir) / "review-receipt.json"
            res = run_gemini_review_lane(
                sandbox=sandbox,
                prompt="review only",
                gemini_binary=_fake_gemini(bindir),
                transcript_path=str(pathlib.Path(bindir) / "gemini.raw.jsonl"),
                review_receipt_path=str(receipt_path),
                env={"GEMINI_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["kind"], "moonweave-review-receipt")
            self.assertEqual(receipt["provider"], "google-gemini")
            self.assertEqual(receipt["can_change_evidence_verdict"], False)
            self.assertEqual(receipt["findings"][0]["severity"], "medium")
            self.assertEqual(res.review_receipt_path, str(receipt_path))

    def test_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(GeminiAdapterError) as cm:
                run_gemini_review_lane(
                    sandbox=sandbox,
                    prompt="review only",
                    gemini_binary=_fake_gemini(bindir),
                    transcript_path=str(pathlib.Path(sandbox) / "gemini.raw.jsonl"),
                    env={"GEMINI_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_review_receipt_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(GeminiAdapterError) as cm:
                run_gemini_review_lane(
                    sandbox=sandbox,
                    prompt="review only",
                    gemini_binary=_fake_gemini(bindir),
                    transcript_path=str(pathlib.Path(bindir) / "gemini.raw.jsonl"),
                    review_receipt_path=str(
                        pathlib.Path(sandbox) / "review-receipt.json"
                    ),
                    env={"GEMINI_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_review_lane_blocks_if_files_change(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_gemini_review_lane(
                sandbox=sandbox,
                prompt="review only",
                gemini_binary=_fake_gemini(bindir, writes_file=True),
                transcript_path=str(pathlib.Path(bindir) / "gemini.raw.jsonl"),
                env={"GEMINI_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertEqual(res.exit_code, 125)
            self.assertEqual(res.test_output["status"], "failed")
            self.assertIn("read-only", res.test_output["summary"])
            self.assertIn("reviewed.txt", res.touched_files)


if __name__ == "__main__":
    unittest.main()
