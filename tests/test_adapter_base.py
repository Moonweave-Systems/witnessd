import unittest

from depone.agent_fabric.paired_run import VALID_RUNNERS, validate_runner_receipt

from witnessd.adapters.base import (
    RUNNER_KIND_BY_ADAPTER,
    AdapterResult,
    RunnerKindError,
    assert_runner_kind_valid,
)


class TestAdapterBase(unittest.TestCase):
    def test_codex_maps_to_codex_cli(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["codex"], "codex-cli")

    def test_claude_opencode_manual_until_extension(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["claude"], "manual")
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["opencode"], "manual")

    def test_all_mapped_kinds_in_depone_valid_runners(self):
        self.assertTrue(set(RUNNER_KIND_BY_ADAPTER.values()) <= VALID_RUNNERS)

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


if __name__ == "__main__":
    unittest.main()
