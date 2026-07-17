import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_no_depone_import.py"


def _run_check(root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(root)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


class TestNoDeponeImport(unittest.TestCase):
    def test_current_runtime_package_passes(self) -> None:
        completed = _run_check(REPO_ROOT)

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_real_import_fails_but_comments_and_strings_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            package = root / "witnessd"
            package.mkdir()
            fixture = package / "fixture.py"
            fixture.write_text(
                '"""witnessd MUST NOT import depone at runtime."""\n'
                "# import depone\n"
                'MESSAGE = "from depone import verifier"\n',
                encoding="utf-8",
            )

            clean = _run_check(root)
            self.assertEqual(clean.returncode, 0, clean.stderr)

            planted = package / "planted.py"
            planted.write_text(
                "def load_verifier():\n"
                "    import depone\n"
                "    from depone.verify import evidence_contract\n"
                "    return depone\n",
                encoding="utf-8",
            )

            blocked = _run_check(root)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("witnessd/planted.py:2", blocked.stderr)
            self.assertIn("import depone", blocked.stderr)
            self.assertIn("witnessd/planted.py:3", blocked.stderr)
            self.assertIn(
                "from depone.verify import evidence_contract", blocked.stderr
            )


if __name__ == "__main__":
    unittest.main()
