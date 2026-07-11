import tempfile
import unittest
from pathlib import Path

from witnessd.changeset import capture_snapshot, diff_snapshots, touched_files


class TestChangesetStateDirExclusion(unittest.TestCase):
    def test_witnessd_state_dir_never_appears_in_snapshot(self):
        # Defense-in-depth for adapter_run.py's fail-closed state_root/sandbox
        # separation guard: even if a .witnessd dir somehow ends up inside an
        # observed sandbox, it must never be captured as part of the diff.
        with tempfile.TemporaryDirectory() as sandbox:
            state_dir = Path(sandbox) / ".witnessd" / "codex-home"
            state_dir.mkdir(parents=True)
            (state_dir / "config.toml").write_text("noise", encoding="utf-8")

            snapshot = capture_snapshot(sandbox)

            self.assertEqual(snapshot, {})

    def test_witnessd_state_dir_changes_do_not_appear_as_touched(self):
        with tempfile.TemporaryDirectory() as sandbox:
            before = capture_snapshot(sandbox)

            (Path(sandbox) / "real-change.txt").write_text("x", encoding="utf-8")
            state_dir = Path(sandbox) / ".witnessd" / "codex-home"
            state_dir.mkdir(parents=True)
            (state_dir / "auth.json").write_text("secret", encoding="utf-8")

            after = capture_snapshot(sandbox)
            touched = touched_files(diff_snapshots(before, after))

            self.assertEqual(touched, ["real-change.txt"])

    def test_ordinary_directories_are_still_captured(self):
        with tempfile.TemporaryDirectory() as sandbox:
            (Path(sandbox) / "pkg").mkdir()
            (Path(sandbox) / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

            snapshot = capture_snapshot(sandbox)

            self.assertIn("pkg/mod.py", snapshot)

    def test_nested_witnessd_named_dir_is_not_hidden(self):
        # The exclusion is scoped to the sandbox root only, matching the
        # exact shape run_adapter_lane's guard defends against
        # (state_dir == sandbox_root/.witnessd). An observed repo that
        # happens to contain its own unrelated ".witnessd"-named directory
        # deeper in its tree (e.g. a vendored sub-project) must still have
        # its real content tracked, not silently hidden.
        with tempfile.TemporaryDirectory() as sandbox:
            nested = Path(sandbox) / "vendor" / "some-project" / ".witnessd"
            nested.mkdir(parents=True)
            (nested / "unrelated.txt").write_text("real content", encoding="utf-8")

            snapshot = capture_snapshot(sandbox)

            self.assertIn("vendor/some-project/.witnessd/unrelated.txt", snapshot)


if __name__ == "__main__":
    unittest.main()
