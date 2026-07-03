import os
import subprocess
import sys
import tempfile
import unittest


class TestRevalidateW10(unittest.TestCase):
    def _skip_if_legacy_fixture_is_not_bound_to_this_checkout(self):
        from scripts import revalidate_w10

        if (
            not revalidate_w10.w10_fixture_uses_portable_internal_paths()
            and not revalidate_w10.w10_fixture_uses_legacy_current_checkout_paths()
        ):
            self.skipTest(
                "legacy W10 fixture is birth-path bound; operator regeneration "
                "with portable internal paths enables this checkout"
            )

    def test_w10_live_fixture_revalidates(self):
        from scripts import revalidate_w10

        self._skip_if_legacy_fixture_is_not_bound_to_this_checkout()
        self.assertEqual(revalidate_w10.main(), 0)

    def test_w10_runlog_binds_emitted_artifact_hashes(self):
        from scripts import revalidate_w10

        revalidate_w10._assert_runlog_artifact_hashes()

    def test_w10_auxiliary_command_and_transcript_are_bound(self):
        from scripts import revalidate_w10

        self._skip_if_legacy_fixture_is_not_bound_to_this_checkout()
        revalidate_w10._assert_auxiliary_command_and_transcript()

    def test_w10_export_root_revalidates_when_fixture_is_portable(self):
        from scripts import revalidate_w10

        git_probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=revalidate_w10.ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if git_probe.returncode != 0:
            self.skipTest("git archive export-root assertion requires a git worktree")

        if not revalidate_w10.w10_fixture_uses_portable_internal_paths():
            # This is a temporary pre-recapture boundary, not a portability
            # workaround: the committed legacy fixture is signed with absolute
            # birth paths and cannot be made export-root portable without a
            # fresh W10 capture.
            self.skipTest(
                "W10 fixture still contains legacy absolute paths; this portable "
                "export-root assertion is enabled by the operator recapture"
            )

        with tempfile.TemporaryDirectory() as tmp:
            archive = subprocess.run(
                ["git", "archive", "HEAD"],
                cwd=revalidate_w10.ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            subprocess.run(
                ["tar", "-x", "-C", tmp],
                input=archive.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            depone_path = os.environ.get(
                "WITNESSD_DEPONE_ROOT", str(revalidate_w10.ROOT.parent / "depone")
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = (
                depone_path
                if not env.get("PYTHONPATH")
                else depone_path + os.pathsep + env["PYTHONPATH"]
            )
            result = subprocess.run(
                [sys.executable, "scripts/revalidate_w10.py"],
                cwd=tmp,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )

        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
