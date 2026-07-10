import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.paired_run import VALID_RUNNERS, validate_runner_receipt

from witnessd.adapters.base import (
    AgentAdapter,
    RUNNER_KIND_BY_ADAPTER,
    AdapterExecutionError,
    AdapterResult,
    RawRun,
    RunnerKindError,
    assert_evidence_path_separated,
    assert_runner_kind_valid,
)


class TestAdapterBase(unittest.TestCase):
    def test_codex_maps_to_codex_cli(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["codex"], "codex-cli")

    def test_claude_opencode_manual_until_extension(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["claude"], "manual")
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["agy"], "manual")
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["opencode"], "manual")

    def test_all_mapped_kinds_in_depone_valid_runners(self):
        self.assertTrue(set(RUNNER_KIND_BY_ADAPTER.values()) <= VALID_RUNNERS)

    def test_agent_adapter_protocol_surface(self):
        class MinimalAdapter:
            provider = "example"

            def compile_invocation(self, intent):
                return ["example", str(intent["run_id"])]

            def run(self, intent, sandbox):
                return RawRun(
                    invocation=self.compile_invocation(intent),
                    exit_code=0,
                    raw_events=b'{"type":"message"}\n',
                    stdout="",
                    stderr="",
                    effective_policy={"approval_policy": "never"},
                )

            def normalize(self, raw):
                return []

            def effective_policy(self, raw):
                return dict(raw.effective_policy)

        adapter: AgentAdapter = MinimalAdapter()
        raw = adapter.run({"run_id": "r1"}, "/tmp")
        self.assertEqual(
            adapter.compile_invocation({"run_id": "r1"}), ["example", "r1"]
        )
        self.assertEqual(adapter.effective_policy(raw), {"approval_policy": "never"})

    def test_unknown_kind_rejected_failclosed(self):
        with self.assertRaises(RunnerKindError):
            assert_runner_kind_valid("claude-code")

    def test_result_requires_nonempty_invocation(self):
        with self.assertRaises(ValueError):
            AdapterResult(
                adapter="codex",
                runner_kind="codex-cli",
                invocation=[],
                exit_code=0,
                transcript_path="t",
                command_receipts=[],
                touched_files=[],
                test_output={"status": "passed"},
            )

    def test_result_converts_to_valid_codex_runner_receipt(self):
        result = AdapterResult(
            adapter="codex",
            runner_kind="codex-cli",
            invocation=["codex", "exec"],
            exit_code=0,
            transcript_path="/tmp/transcript.txt",
            command_receipts=[{"command": ["codex", "exec"], "exit_code": 0}],
            touched_files=[],
            test_output={"status": "not-run"},
        )

        receipt = result.to_runner_receipt(
            arm="direct",
            task_id="task-1",
            worktree="/tmp/worktree",
            started_at="2026-07-01T00:00:00Z",
            ended_at="2026-07-01T00:00:01Z",
        )

        self.assertEqual(validate_runner_receipt(receipt), [])
        self.assertEqual(receipt["runner_kind"], "codex-cli")

    def test_evidence_path_inside_sandbox_rejected_failclosed(self):
        with tempfile.TemporaryDirectory() as sandbox:
            with self.assertRaises(AdapterExecutionError) as cm:
                assert_evidence_path_separated(
                    sandbox, str(Path(sandbox) / "transcript.txt")
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_evidence_path_outside_sandbox_accepted(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence_dir,
        ):
            assert_evidence_path_separated(
                sandbox, str(Path(evidence_dir) / "transcript.txt")
            )

    def test_evidence_path_error_uses_caller_error_class(self):
        class CustomAdapterError(AdapterExecutionError):
            pass

        with tempfile.TemporaryDirectory() as sandbox:
            with self.assertRaises(CustomAdapterError) as cm:
                assert_evidence_path_separated(
                    sandbox,
                    str(Path(sandbox) / "transcript.txt"),
                    error_cls=CustomAdapterError,
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")


if __name__ == "__main__":
    unittest.main()
