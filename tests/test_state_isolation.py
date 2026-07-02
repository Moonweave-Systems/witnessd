import os
import tempfile
import unittest

from witnessd.state import (
    StateContentionError,
    StateNamespace,
    detect_state_contention,
)


class TestStateIsolation(unittest.TestCase):
    def test_only_writes_own_namespace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as omx:
            before = set(os.listdir(omx))

            with StateNamespace(root) as namespace:
                self.assertTrue(
                    namespace.runlog_path.startswith(os.path.join(root, ".witnessd"))
                )
                env = namespace.codex_env(base_env={"HOME": omx})
                self.assertNotEqual(env["CODEX_HOME"], omx)
                self.assertTrue(env["CODEX_HOME"].startswith(root))

            self.assertEqual(set(os.listdir(omx)), before)

    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as root:
            with StateNamespace(root):
                with self.assertRaises(StateContentionError):
                    StateNamespace(root).__enter__()

    def test_doctor_detects_overlap(self):
        with tempfile.TemporaryDirectory() as root:
            worktree = os.path.join(root, "wt")
            errors = detect_state_contention(
                witnessd_worktree=worktree,
                external_active_worktrees=[worktree],
            )

            self.assertIn("ERR_WITNESSD_STATE_CONTENTION", errors[0])


if __name__ == "__main__":
    unittest.main()
