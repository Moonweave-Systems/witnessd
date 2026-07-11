import os
import stat
import tempfile
import unittest
from pathlib import Path

from witnessd.state import (
    StateContentionError,
    StateNamespace,
    detect_state_contention,
)


class TestStateIsolation(unittest.TestCase):
    def test_only_writes_own_namespace(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as omx,
        ):
            before = set(os.listdir(omx))

            with StateNamespace(root) as namespace:
                self.assertTrue(
                    os.path.realpath(namespace.runlog_path).startswith(
                        os.path.realpath(os.path.join(root, ".witnessd"))
                    )
                )
                env = namespace.codex_env(base_env={"HOME": omx})
                self.assertNotEqual(env["CODEX_HOME"], omx)
                self.assertTrue(
                    os.path.realpath(env["CODEX_HOME"]).startswith(
                        os.path.realpath(root)
                    )
                )

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

    def test_codex_env_seeds_isolated_home_with_ambient_auth(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as fake_home,
        ):
            ambient_codex_home = Path(fake_home) / ".codex"
            ambient_codex_home.mkdir()
            (ambient_codex_home / "auth.json").write_text(
                '{"tokens": "secret"}', encoding="utf-8"
            )

            with StateNamespace(root) as namespace:
                env = namespace.codex_env(base_env={"HOME": fake_home})

            seeded = Path(env["CODEX_HOME"]) / "auth.json"
            self.assertTrue(seeded.exists())
            self.assertEqual(seeded.read_text(encoding="utf-8"), '{"tokens": "secret"}')
            self.assertEqual(stat.S_IMODE(seeded.stat().st_mode), 0o600)
            # The ambient auth.json is only ever read, never mutated.
            self.assertEqual(
                (ambient_codex_home / "auth.json").read_text(encoding="utf-8"),
                '{"tokens": "secret"}',
            )

    def test_codex_env_does_not_clobber_an_already_staged_auth_file(self):
        # Mirrors `witnessd team run --codex-auth-source ...`: an explicit,
        # deliberate auth.json is staged into state_dir/codex-home before the
        # lane runs. codex_env()'s ambient fallback must never overwrite it.
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as fake_home,
        ):
            ambient_codex_home = Path(fake_home) / ".codex"
            ambient_codex_home.mkdir()
            (ambient_codex_home / "auth.json").write_text(
                '{"tokens": "ambient"}', encoding="utf-8"
            )

            with StateNamespace(root) as namespace:
                staged = namespace.state_dir / "codex-home"
                staged.mkdir(parents=True)
                (staged / "auth.json").write_text(
                    '{"tokens": "explicitly-staged"}', encoding="utf-8"
                )

                env = namespace.codex_env(base_env={"HOME": fake_home})

            seeded = Path(env["CODEX_HOME"]) / "auth.json"
            self.assertEqual(
                seeded.read_text(encoding="utf-8"), '{"tokens": "explicitly-staged"}'
            )

    def test_codex_env_without_ambient_auth_is_a_graceful_noop(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as fake_home,
        ):
            with StateNamespace(root) as namespace:
                env = namespace.codex_env(base_env={"HOME": fake_home})

            self.assertFalse((Path(env["CODEX_HOME"]) / "auth.json").exists())

    def test_doctor_detects_overlap_through_symlink_alias(self):
        with tempfile.TemporaryDirectory() as root:
            real_parent = os.path.join(root, "real")
            alias_parent = os.path.join(root, "alias")
            os.mkdir(real_parent)
            os.symlink(real_parent, alias_parent)
            worktree = os.path.join(alias_parent, "wt")
            nested = os.path.join(real_parent, "wt", "child")

            errors = detect_state_contention(
                witnessd_worktree=worktree,
                external_active_worktrees=[nested],
            )

            self.assertIn("ERR_WITNESSD_STATE_CONTENTION", errors[0])


if __name__ == "__main__":
    unittest.main()
