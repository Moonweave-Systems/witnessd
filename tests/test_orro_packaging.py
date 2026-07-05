from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OrroPackagingTests(unittest.TestCase):
    def test_editable_install_exposes_orro_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "venv"
            venv.EnvBuilder(with_pip=True).create(venv_dir)
            python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            orro = venv_dir / ("Scripts/orro.exe" if os.name == "nt" else "bin/orro")

            install = subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-build-isolation",
                    "-e",
                    str(ROOT),
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertTrue(orro.is_file())

            help_result = subprocess.run(
                [str(orro), "--help"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("ORRO Flow", help_result.stdout)
            self.assertIn("engine-lock", help_result.stdout)
            self.assertNotIn("self-test", help_result.stdout)

            root = tmp_path / "repo"
            root.mkdir()
            flowplan = subprocess.run(
                [str(orro), "flowplan", "package smoke", "--root", str(root)],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)
            self.assertEqual(json.loads(flowplan.stdout)["sealed_plan"]["goal"], "package smoke")

            lock = subprocess.run(
                [str(orro), "engine-lock", "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(lock.returncode, 2)
            self.assertEqual(
                json.loads(lock.stdout)["error"]["code"],
                "ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
            )


if __name__ == "__main__":
    unittest.main()
