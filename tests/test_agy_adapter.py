import hashlib
import json
import pathlib
import stat
import subprocess
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.engine import _read_review_signals

from witnessd.adapters.agy import AgyAdapterError, AgyCLIAdapter, run_agy_review_lane


def _seed_repo(directory: pathlib.Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agy-test@example.invalid"],
        cwd=directory,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AGY Test"], cwd=directory, check=True
    )
    (directory / "README.md").write_text("# review fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=directory, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fake_agy(directory: str, *, writes_file: bool = False) -> str:
    path = pathlib.Path(directory) / "agy"
    write_command = (
        "pathlib.Path('reviewed.txt').write_text('changed\\n', encoding='utf-8')\n"
        if writes_file
        else ""
    )
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "capture = os.environ.get('AGY_ARGV_CAPTURE')\n"
        "if capture:\n"
        "    pathlib.Path(capture).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n"
        f"{write_command}"
        "if sys.stdout.isatty():\n"
        "    mode = os.environ.get('AGY_CONTEXT_MODE', 'correct')\n"
        "    if mode != 'missing':\n"
        "        observed_root = os.environ.get('AGY_OBSERVED_REPO', os.getcwd())\n"
        "        observed_head = os.environ.get('AGY_OBSERVED_HEAD')\n"
        "        if observed_head is None:\n"
        "            observed_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=observed_root, check=True, capture_output=True, text=True).stdout.strip()\n"
        "        marker = 'WITNESSD_AGY_CONTEXT ' + json.dumps({'repo_root': observed_root, 'git_head': observed_head}, sort_keys=True)\n"
        "        if mode == 'malformed':\n"
        "            marker = 'WITNESSD_AGY_CONTEXT not-json'\n"
        "        print(marker)\n"
        "        if mode == 'duplicate':\n"
        "            print(marker)\n"
        "    print('Review findings:')\n"
        "    print('medium pkg/a.py:7 check edge case')\n"
        "else:\n"
        "    print('non-tty-final-response-lost', file=sys.stderr)\n",
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
                [
                    _fake_agy(bindir),
                    "-p",
                    "review only",
                    "--mode",
                    "plan",
                    "--sandbox",
                    "--new-project",
                ],
            )
            self.assertNotIn("--output-format", invocation)
            self.assertNotIn("--dangerously-skip-permissions", invocation)

    def test_review_lane_uses_pty_and_read_only_policy(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
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
            self.assertEqual(res.invocation.count("--new-project"), 1)
            self.assertEqual(res.invocation.count("--add-dir"), 1)
            self.assertEqual(
                res.invocation[res.invocation.index("--add-dir") + 1],
                str(pathlib.Path(sandbox).resolve()),
            )
            self.assertIn("--mode", res.invocation)
            self.assertEqual(res.invocation[res.invocation.index("--mode") + 1], "plan")
            self.assertIn("--sandbox", res.invocation)
            self.assertNotIn("--output-format", res.invocation)
            self.assertNotIn("--dangerously-skip-permissions", res.invocation)
            self.assertIn("-p", res.invocation)
            self.assertEqual(res.test_output, {"status": "not-run"})
            self.assertIn(
                b"Review findings:", pathlib.Path(res.transcript_path).read_bytes()
            )
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

    def test_read_only_review_lane_with_separated_evidence_reports_empty_touched_files(
        self,
    ):
        # Live-bug regression: an agy review lane run with transcript_path,
        # events.normalized.jsonl, and review-receipt.json inside the sandbox
        # reported touched_files == ['events.normalized.jsonl',
        # 'events.raw.jsonl', 'review-receipt.json'] for a read-only lane.
        # With evidence paths correctly separated from the sandbox, none of
        # the adapter's own evidence artifacts may appear in touched_files.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                review_receipt_path=str(pathlib.Path(bindir) / "review-receipt.json"),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertEqual(res.exit_code, 0)
            self.assertEqual(res.touched_files, [])

    def test_agy_text_response_normalizes_to_single_agent_event_envelope(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
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
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            head = _seed_repo(pathlib.Path(sandbox))
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
            binding = receipt["context_binding"]
            self.assertEqual(binding["status"], "bound")
            self.assertEqual(binding["requested_project_identity"], binding["observed_project_identity"])
            self.assertEqual(binding["canonical_repo_root"], str(pathlib.Path(sandbox).resolve()))
            self.assertEqual(binding["observed_repo_root"], str(pathlib.Path(sandbox).resolve()))
            self.assertEqual(binding["requested_git_head_sha"], head)
            self.assertEqual(binding["observed_git_head_sha"], head)
            self.assertTrue(receipt["findings_usable"])
            self.assertEqual(receipt["findings"][0]["severity"], "medium")
            self.assertIn("WITNESSD_AGY_CONTEXT ", receipt["raw_output_text"])
            self.assertEqual(res.review_receipt_path, str(receipt_path))

    def test_stale_project_context_is_invalid_and_findings_are_unusable(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as stale_repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            _seed_repo(pathlib.Path(stale_repo))
            receipt_path = pathlib.Path(bindir) / "review-receipt.json"
            log_path = pathlib.Path(bindir) / "command-log.json"
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                review_receipt_path=str(receipt_path),
                log_path=str(log_path),
                env={
                    "AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt"),
                    "AGY_OBSERVED_REPO": stale_repo,
                },
            )

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(res.exit_code, 126)
            self.assertEqual(res.test_output["status"], "failed")
            self.assertEqual(res.test_output["error_code"], "ERR_AGY_INVALID_CONTEXT")
            self.assertEqual(receipt["context_binding"]["status"], "invalid-context")
            self.assertEqual(
                receipt["kind"], "moonweave-review-context-diagnostic"
            )
            self.assertNotEqual(
                receipt["context_binding"]["requested_project_identity"],
                receipt["context_binding"]["observed_project_identity"],
            )
            self.assertEqual(receipt["findings"], [])
            self.assertFalse(receipt["findings_usable"])
            self.assertFalse(receipt["usable_as_review_evidence"])
            self.assertFalse(receipt["usable_as_implementation_guidance"])
            self.assertNotIn("check edge case", json.dumps(receipt))
            self.assertEqual(res.normalized_events, [])
            self.assertIsNone(res.raw_events_path)
            self.assertIsNone(res.normalized_events_path)
            self.assertNotIn("check edge case", json.dumps(res.command_receipts))
            self.assertEqual(pathlib.Path(res.transcript_path).read_bytes(), b"")
            self.assertNotIn(
                "check edge case", log_path.read_text(encoding="utf-8")
            )
            receipt_text = receipt_path.read_text(encoding="utf-8")
            review_signals = _read_review_signals(
                EvidenceContext(
                    run_id="agy-invalid-context",
                    files=[
                        EvidenceFile(
                            path="review-receipt.json",
                            content=receipt_text,
                            sha256=hashlib.sha256(receipt_text.encode()).hexdigest(),
                        )
                    ],
                    raw={},
                )
            )
            self.assertEqual(review_signals, [])

    def test_missing_context_marker_is_invalid_context(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={
                    "AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt"),
                    "AGY_CONTEXT_MODE": "missing",
                },
            )

            self.assertEqual(res.exit_code, 126)
            self.assertEqual(res.test_output["error_code"], "ERR_AGY_INVALID_CONTEXT")

    def test_malformed_or_duplicate_context_marker_is_invalid_context(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            for mode in ("malformed", "duplicate"):
                with self.subTest(mode=mode):
                    res = run_agy_review_lane(
                        sandbox=sandbox,
                        prompt="review only",
                        agy_binary=_fake_agy(bindir),
                        transcript_path=str(pathlib.Path(bindir) / f"{mode}.raw.jsonl"),
                        env={"AGY_CONTEXT_MODE": mode},
                    )

                    self.assertEqual(res.exit_code, 126)
                    self.assertEqual(
                        res.test_output["error_code"], "ERR_AGY_INVALID_CONTEXT"
                    )

    def test_repository_without_confirmable_head_is_invalid_without_launch(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            argv_capture = pathlib.Path(bindir) / "argv.txt"
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(argv_capture)},
            )

            self.assertEqual(res.exit_code, 126)
            self.assertEqual(res.test_output["error_code"], "ERR_AGY_INVALID_CONTEXT")
            self.assertFalse(argv_capture.exists())

    def test_forbidden_write_approval_flags_fail_closed_before_launch(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            for extra_args in (
                ["--dangerously-skip-permissions"],
                ["--mode", "accept-edits"],
                ["--output-format", "json"],
                ["--project", "ambient-project"],
                ["--new-project"],
                ["--add-dir", "/tmp/wrong"],
            ):
                with self.subTest(extra_args=extra_args):
                    with self.assertRaises(AgyAdapterError) as cm:
                        run_agy_review_lane(
                            sandbox=sandbox,
                            prompt="review only",
                            agy_binary=_fake_agy(bindir),
                            transcript_path=str(pathlib.Path(bindir) / "agy.raw.txt"),
                            extra_args=extra_args,
                            env={
                                "AGY_ARGV_CAPTURE": str(
                                    pathlib.Path(bindir) / "argv.txt"
                                )
                            },
                        )
                    self.assertEqual(cm.exception.code, "ERR_AGY_FORBIDDEN_FLAG")

    def test_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(AgyAdapterError) as cm:
                run_agy_review_lane(
                    sandbox=sandbox,
                    prompt="review only",
                    agy_binary=_fake_agy(bindir),
                    transcript_path=str(pathlib.Path(sandbox) / "agy.raw.jsonl"),
                    env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_review_receipt_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(AgyAdapterError) as cm:
                run_agy_review_lane(
                    sandbox=sandbox,
                    prompt="review only",
                    agy_binary=_fake_agy(bindir),
                    transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                    review_receipt_path=str(
                        pathlib.Path(sandbox) / "review-receipt.json"
                    ),
                    env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_review_lane_blocks_if_files_change(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
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

    def test_no_write_enforcement_takes_precedence_over_context_mismatch(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as stale_repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            _seed_repo(pathlib.Path(stale_repo))
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir, writes_file=True),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_OBSERVED_REPO": stale_repo},
            )

            self.assertEqual(res.exit_code, 125)
            self.assertNotIn("error_code", res.test_output)
            self.assertIn("read-only", res.test_output["summary"])
            self.assertIn("reviewed.txt", res.touched_files)

    def test_no_model_requested_emits_no_declaration(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
            )

            self.assertIsNone(res.model_declaration)

    def test_requested_model_is_always_reported_unverified(self):
        # agy's --model has no rejection signal at all (live-verified: an
        # invalid model silently falls back to a default with no error), so
        # a requested model can never be marked "verified" -- only that it
        # was asked for.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _seed_repo(pathlib.Path(sandbox))
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="review only",
                agy_binary=_fake_agy(bindir),
                transcript_path=str(pathlib.Path(bindir) / "agy.raw.jsonl"),
                env={"AGY_ARGV_CAPTURE": str(pathlib.Path(bindir) / "argv.txt")},
                model="gemini-3.5-flash",
            )

            self.assertEqual(
                res.model_declaration,
                {
                    "kind": "moonweave-model-declaration",
                    "schema_version": "1.0",
                    "can_change_evidence_verdict": False,
                    "adapter": "agy",
                    "requested_model": "gemini-3.5-flash",
                    "verification_status": "requested-unverified",
                    "detail": None,
                },
            )
            argv = pathlib.Path(bindir, "argv.txt").read_text(encoding="utf-8")
            self.assertIn("--model", argv)
            self.assertIn("gemini-3.5-flash", argv)


if __name__ == "__main__":
    unittest.main()
