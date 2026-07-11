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


if __name__ == "__main__":
    unittest.main()
