import pathlib
import stat
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.claude import run_claude_lane
from witnessd.adapters.opencode import run_opencode_lane


def _fake_cli(directory: str, name: str) -> str:
    path = pathlib.Path(directory) / name
    path.write_text(
        "#!/bin/sh\n"
        "echo ran >&2\n"
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
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_cli(bindir, "claude"),
                transcript_path=str(pathlib.Path(bindir) / "claude.txt"),
            )

            self._check(res, "claude", sandbox)
            self.assertIn("-p", res.invocation)

    def test_opencode(self):
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            res = run_opencode_lane(
                sandbox=sandbox,
                prompt="x",
                opencode_binary=_fake_cli(bindir, "opencode"),
                transcript_path=str(pathlib.Path(bindir) / "opencode.txt"),
            )

            self._check(res, "opencode", sandbox)
            self.assertIn("run", res.invocation)


if __name__ == "__main__":
    unittest.main()
