import os
import tempfile
import unittest

from witnessd.adapters.shell import run_shell_lane


class TestShell(unittest.TestCase):
    def test_receipts_shape(self):
        with tempfile.TemporaryDirectory() as sandbox:
            result = run_shell_lane(
                sandbox=sandbox, commands=[["sh", "-c", "echo hi > f.txt"]]
            )
            self.assertTrue(result["command_receipts"])
            self.assertEqual(
                result["command_receipts"][0]["command"],
                ["sh", "-c", "echo hi > f.txt"],
            )
            self.assertIsInstance(result["command_receipts"][0]["exit_code"], int)
            self.assertIn(
                result["test_output"]["status"],
                {"not-run", "passed", "failed", "error"},
            )

    def test_touched_files_diff(self):
        with tempfile.TemporaryDirectory() as sandbox:
            result = run_shell_lane(
                sandbox=sandbox, commands=[["sh", "-c", "echo hi > created.txt"]]
            )
            self.assertIn("created.txt", result["touched_files"])
            self.assertEqual(result["command_receipts"][0]["exit_code"], 0)

    def test_nonzero_exit_recorded(self):
        with tempfile.TemporaryDirectory() as sandbox:
            result = run_shell_lane(sandbox=sandbox, commands=[["sh", "-c", "exit 3"]])
            self.assertEqual(result["command_receipts"][0]["exit_code"], 3)

    def test_test_command_classifies_status(self):
        with tempfile.TemporaryDirectory() as sandbox:
            passed = run_shell_lane(
                sandbox=sandbox, commands=[], test_command=["sh", "-c", "true"]
            )
            self.assertEqual(passed["test_output"]["status"], "passed")
            failed = run_shell_lane(
                sandbox=sandbox, commands=[], test_command=["sh", "-c", "false"]
            )
            self.assertEqual(failed["test_output"]["status"], "failed")

    def test_default_status_not_run(self):
        with tempfile.TemporaryDirectory() as sandbox:
            result = run_shell_lane(sandbox=sandbox, commands=[["sh", "-c", "true"]])
            self.assertEqual(result["test_output"]["status"], "not-run")

    def test_runs_in_sandbox_cwd(self):
        with tempfile.TemporaryDirectory() as sandbox:
            run_shell_lane(
                sandbox=sandbox, commands=[["sh", "-c", "echo x > here.txt"]]
            )
            self.assertTrue(os.path.exists(os.path.join(sandbox, "here.txt")))


if __name__ == "__main__":
    unittest.main()
