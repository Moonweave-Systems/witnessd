import json
import pathlib
import stat
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.agy import AgyAdapterError, AgyCLIAdapter, run_agy_review_lane


def _fake_agy(directory: str, *, writes_file: bool = False) -> str:
    path = pathlib.Path(directory) / "agy"
    write_command = "printf 'changed\\n' > reviewed.txt\n" if writes_file else ""
    path.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$AGY_ARGV_CAPTURE\"\n"
        f"{write_command}"
        "if [ -t 1 ]; then\n"
        "  printf '%s\\n' 'Review findings:'\n"
        "  printf '%s\\n' 'medium pkg/a.py:7 check edge case'\n"
        "else\n"
        "  printf '%s\\n' 'non-tty-final-response-lost' >&2\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestAgyAdapter(unittest.TestCase):
    def test_compile_invocation_matches_agy_v111_read_only_flags(self):
        with tempfile.TemporaryDirectory() as bindir:
            adapter = AgyCLIAdapter(agy_binary=_fake_agy(bindir))
            invocation = adapter.compile_invocation({"prompt": "review only"})

            self.assertEqual(
                invocation,
                [_fake_agy(bindir), "-p", "review only", "--mode", "plan", "--sandbox"],
            )
            self.assertNotIn("--output-format", invocation)
            self.assertNotIn("--dangerously-skip-permissions", invocation)

    def test_review_lane_uses_pty_and_read_only_policy(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            argv_capture = pathlib.Path(bindir) / "argv.txt"
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(argv_capture)},
            )

            self.assertEqual(res.runner_kind, "manual")
            self.assertEqual(res.exit_code, 0)
            self.assertEqual(
                res.invocation,
                [
                    _fake_agy(bindir),
                    "-p",
                    "review only",
                    "--mode",
                    "plan",
                    "--sandbox",
                ],
            )
            self.assertIn("--mode", res.invocation)
            self.assertEqual(res.invocation[res.invocation.index("--mode") + 1], "plan")
            self.assertIn("--sandbox", res.invocation)
            self.assertNotIn("--output-format", res.invocation)
            self.assertNotIn("--dangerously-skip-permissions", res.invocation)
            self.assertIn("-p", res.invocation)
            self.assertEqual(res.test_output, {"status": "not-run"})
            self.assertIn(b"Review findings:", pathlib.Path(res.transcript_path).read_bytes())
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

    def test_agy_text_response_normalizes_to_single_agent_event_envelope(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertEqual(
                {event["schema"] for event in res.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            self.assertEqual(
                {event["provider"] for event in res.normalized_events},
                {"google-antigravity"},
            )
            self.assertEqual(
                [event["event_type"] for event in res.normalized_events],
                ["message.completed"],
            )
            self.assertEqual(len(res.normalized_events), 1)
            self.assertTrue((pathlib.Path(bindir) / "events.normalized.jsonl").exists())

    def test_review_receipt_reuses_advisory_review_signal_contract(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            receipt_path = pathlib.Path(bindir) / "review-receipt.json"
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                review_receipt_path=str(receipt_path),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["kind"], "moonweave-review-receipt")
            self.assertEqual(receipt["provider"], "google-antigravity")
            self.assertEqual(receipt["can_change_evidence_verdict"], False)
            self.assertEqual(receipt["findings"][0]["severity"], "medium")
            self.assertEqual(receipt["raw_output_text"], "Review findings:\r\nmedium pkg/a.py:7 check edge case\r\n")
            self.assertEqual(res.review_receipt_path, str(receipt_path))

    def test_forbidden_write_approval_flags_fail_closed_before_launch(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            for extra_args in (
                ["--dangerously-skip-permissions"],
                ["--mode", "accept-edits"],
                ["--output-format", "json"],
            ):
                with self.subTest(extra_args=extra_args):
                    with self.assertRaises(AgyAdapterError) as cm:
                        run_agy_review_lane(
                            sandbox=sandbox,
                            prompt="review only",
                            agy_binary=_fake_agy(bindir),
                            transcript_path=str(pathlib.Path(bindir) / "agy.raw.txt"),
                            extra_args=extra_args,
                            env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                        )
                    self.assertEqual(cm.exception.code, "ERR_AGY_FORBIDDEN_FLAG")

    def test_review_lane_blocks_if_files_change(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir, writes_file=True),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertEqual(res.exit_code, 125)
            self.assertEqual(res.test_output["status"], "failed")
            self.assertIn("read-only", res.test_output["summary"])
            self.assertIn("reviewed.txt", res.touched_files)


if __name__ == "__main__":
    unittest.main()
