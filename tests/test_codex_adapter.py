import os
import pathlib
import stat
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.codex import CodexAdapterError, run_codex_lane


def _fake_codex(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_policy_probe(directory: str, effective_policy: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "policy=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--ask-for-approval" ]; then shift; policy="$1"; shift; continue; fi\n'
        "  shift\n"
        "done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        f'printf \'%s\\n\' \'{{"type":"effective.settings","approval_policy":"{effective_policy}"}}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_model_probe(directory: str, *, reject_model: str | None = None) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "model=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "-m" ]; then shift; model="$1"; shift; continue; fi\n'
        "  shift\n"
        "done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        f'if [ "$model" = "{reject_model}" ]; then\n'
        "  printf '%s\\n' '{\"type\":\"turn.started\"}'\n"
        '  printf \'%s\\n\' \'{"type":"turn.failed","error":{"message":'
        '"the requested model is not supported when using Codex with a '
        "ChatGPT account.\"}}'\n"
        "else\n"
        '  printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestCodexAdapter(unittest.TestCase):
    def test_result_shape_and_receipt_valid(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
            tempfile.TemporaryDirectory() as obs,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex(bindir),
                transcript_path=os.path.join(obs, "transcript.txt"),
                log_path=os.path.join(obs, "codex.log"),
                sandbox_mode="workspace-write",
                allowed_touched_files=["allowed.txt"],
            )

            self.assertEqual(res.runner_kind, "codex-cli")
            self.assertTrue(res.invocation and res.invocation[0].endswith("codex"))
            self.assertIn("exec", res.invocation)
            self.assertIn("--json", res.invocation)
            self.assertIn("--ask-for-approval", res.invocation)
            self.assertEqual(res.exit_code, 0)
            self.assertEqual(res.test_output, {"status": "not-run"})
            self.assertEqual(len(res.normalized_events), 2)
            self.assertEqual(
                [event["event_type"] for event in res.normalized_events],
                ["thread.started", "message.completed"],
            )
            self.assertEqual(
                {event["schema"] for event in res.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            normalized = pathlib.Path(obs) / "events.normalized.jsonl"
            self.assertTrue(normalized.exists())

            receipt = res.to_runner_receipt(
                arm="direct",
                task_id="t1",
                worktree=repo,
                started_at="2026-07-01T00:00:00Z",
                ended_at="2026-07-01T00:00:01Z",
            )

            self.assertEqual(validate_runner_receipt(receipt), [])
            self.assertEqual(receipt["runner_kind"], "codex-cli")

    def test_empty_prompt_rejected(self):
        with self.assertRaises(CodexAdapterError) as cm:
            run_codex_lane(
                sandbox="/tmp",
                prompt="   ",
                codex_binary="/bin/true",
                transcript_path="/tmp/transcript.txt",
            )

        self.assertEqual(cm.exception.code, "ERR_CODEX_PROMPT_MISSING")

    def test_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(CodexAdapterError) as cm:
                run_codex_lane(
                    sandbox=repo,
                    prompt="do X",
                    codex_binary=_fake_codex(bindir),
                    transcript_path=os.path.join(repo, "events.raw.jsonl"),
                    sandbox_mode="workspace-write",
                    allowed_touched_files=["allowed.txt"],
                )

        self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_approval_policy_passed_to_codex_argv(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex_policy_probe(bindir, "untrusted"),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                approval_policy="untrusted",
                allowed_touched_files=["allowed.txt"],
            )

        self.assertIn("--ask-for-approval", res.invocation)
        self.assertEqual(
            res.invocation[res.invocation.index("--ask-for-approval") + 1],
            "untrusted",
        )
        self.assertEqual(res.exit_code, 0)

    def test_effective_approval_policy_mismatch_fails_closed(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex_policy_probe(bindir, "never"),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                approval_policy="untrusted",
                allowed_touched_files=["allowed.txt"],
            )

        self.assertEqual(res.exit_code, 125)
        self.assertEqual(
            res.test_output,
            {
                "status": "failed",
                "summary": "effective approval_policy never != declared untrusted",
            },
        )

    def test_model_passed_to_codex_argv(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex_model_probe(bindir),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                allowed_touched_files=["allowed.txt"],
                model="gpt-5.5",
            )

        self.assertIn("-m", res.invocation)
        self.assertEqual(res.invocation[res.invocation.index("-m") + 1], "gpt-5.5")
        # -m must land before exec (codex's model flag is a top-level option,
        # not an exec subcommand option -- same shape as --ask-for-approval).
        self.assertLess(res.invocation.index("-m"), res.invocation.index("exec"))

    def test_no_model_requested_emits_no_declaration(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex(bindir),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                allowed_touched_files=["allowed.txt"],
            )

        self.assertNotIn("-m", res.invocation)
        self.assertIsNone(res.model_declaration)

    def test_valid_model_reports_verified(self):
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex_model_probe(bindir, reject_model="bad-model"),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                allowed_touched_files=["allowed.txt"],
                model="good-model",
            )

        self.assertEqual(res.exit_code, 0)
        self.assertEqual(
            res.model_declaration,
            {
                "kind": "moonweave-model-declaration",
                "schema_version": "1.0",
                "can_change_evidence_verdict": False,
                "adapter": "codex",
                "requested_model": "good-model",
                "verification_status": "verified",
                "detail": None,
            },
        )

    def test_invalid_model_rejected_by_codex_fails_closed(self):
        # Live-verified against real codex-cli 0.144.1: an invalid -m value
        # produces a turn.failed event naming the model as unsupported. The
        # lane must fail closed rather than silently continuing on whatever
        # model codex actually used.
        with (
            tempfile.TemporaryDirectory() as repo,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_codex_lane(
                sandbox=repo,
                prompt="do X",
                codex_binary=_fake_codex_model_probe(bindir, reject_model="bad-model"),
                transcript_path=os.path.join(bindir, "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                allowed_touched_files=["allowed.txt"],
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
