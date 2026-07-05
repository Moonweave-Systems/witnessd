from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return ROOT.parent / "depone"


class OrroPackagingTests(unittest.TestCase):
    def test_editable_install_exposes_orro_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "venv"
            venv.EnvBuilder(system_site_packages=True, with_pip=True).create(venv_dir)
            python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            orro = venv_dir / ("Scripts/orro.exe" if os.name == "nt" else "bin/orro")
            has_setuptools = subprocess.run(
                [str(python), "-c", "import setuptools"],
                text=True,
                capture_output=True,
                check=False,
            )
            if has_setuptools.returncode != 0:
                self.skipTest("setuptools is unavailable in this Python packaging baseline")

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
            self.assertIn("init", help_result.stdout)
            self.assertIn("advise", help_result.stdout)
            self.assertIn("next", help_result.stdout)
            self.assertIn("report", help_result.stdout)
            self.assertIn("auto", help_result.stdout)
            self.assertIn("engine-lock", help_result.stdout)
            self.assertNotIn("self-test", help_result.stdout)

            advise = subprocess.run(
                [str(orro), "advise", "review this PR", "--repo", str(tmp_path), "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(advise.returncode, 0, advise.stderr)
            advise_payload = json.loads(advise.stdout)
            self.assertEqual(advise_payload["kind"], "orro-workstyle-decision")
            self.assertEqual(advise_payload["task_class"], "review-only")
            self.assertFalse(advise_payload["boundary"]["executes_commands"])

            missing_next = subprocess.run(
                [str(orro), "next", str(tmp_path / "missing-run"), "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing_next.returncode, 2)
            missing_payload = json.loads(missing_next.stdout)
            self.assertEqual(missing_payload["decision"], "invalid-run-dir")
            self.assertFalse(missing_payload["boundary"]["executes_commands"])

            missing_auto = subprocess.run(
                [str(orro), "auto", "--dry-run", str(tmp_path / "missing-run"), "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing_auto.returncode, 2)
            auto_payload = json.loads(missing_auto.stdout)
            self.assertEqual(auto_payload["kind"], "orro-auto-plan")
            self.assertEqual(auto_payload["continuation_decision"]["decision"], "invalid-run-dir")
            self.assertEqual(auto_payload["would_run"], [])
            self.assertFalse(auto_payload["boundary"]["executes_commands"])

            missing_auto_once = subprocess.run(
                [str(orro), "auto", "--once", str(tmp_path / "missing-run"), "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing_auto_once.returncode, 2)
            once_payload = json.loads(missing_auto_once.stdout)
            self.assertEqual(once_payload["kind"], "orro-auto-receipt")
            self.assertEqual(once_payload["decision_before"], "invalid-run-dir")
            self.assertFalse(once_payload["executed"])
            self.assertEqual(once_payload["command"], [])
            self.assertFalse(once_payload["boundary"]["launches_workers"])

            missing_auto_until_complete = subprocess.run(
                [
                    str(orro),
                    "auto",
                    "--until-complete",
                    str(tmp_path / "missing-run"),
                    "--max-steps",
                    "2",
                    "--json",
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing_auto_until_complete.returncode, 2)
            session_payload = json.loads(missing_auto_until_complete.stdout)
            self.assertEqual(session_payload["kind"], "orro-auto-session")
            self.assertEqual(session_payload["decision_initial"], "invalid-run-dir")
            self.assertEqual(session_payload["steps"], [])
            self.assertFalse(session_payload["boundary"]["launches_workers"])

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

            role_lanes = tmp_path / "role-lane-plan.json"
            flowplan_role_lanes = subprocess.run(
                [
                    str(orro),
                    "flowplan",
                    "package smoke",
                    "--root",
                    str(root),
                    "--profile",
                    "code-change",
                    "--role-lanes-out",
                    str(role_lanes),
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(flowplan_role_lanes.returncode, 0, flowplan_role_lanes.stderr)
            self.assertTrue(role_lanes.is_file())
            self.assertEqual(
                json.loads(role_lanes.read_text(encoding="utf-8"))["kind"],
                "orro-role-lane-plan",
            )

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

            home = tmp_path / "home"
            init = subprocess.run(
                [
                    str(orro),
                    "init",
                    "--home",
                    str(home),
                    "--depone-root",
                    str(_depone_root()),
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((home / "provision.json").is_file())

            doctor = subprocess.run(
                [str(orro), "doctor", "--home", str(home), "--json"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertIn(doctor.returncode, {0, 1}, doctor.stderr)
            self.assertEqual(json.loads(doctor.stdout)["command"], "orro doctor")

            lock_path = tmp_path / "orro-engine-lock.json"
            write_lock = subprocess.run(
                [str(orro), "engine-lock", "--home", str(home), "--out", str(lock_path)],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(write_lock.returncode, 0, write_lock.stderr)
            self.assertTrue(lock_path.is_file())

            check_lock = subprocess.run(
                [
                    str(orro),
                    "engine-lock",
                    "--home",
                    str(home),
                    "--check",
                    str(lock_path),
                    "--json",
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(check_lock.returncode, 0, check_lock.stderr)
            self.assertTrue(json.loads(check_lock.stdout)["locked"])

            mismatched = tmp_path / "mismatched-lock.json"
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            payload["witnessd"]["commit"] = "0" * 40
            mismatched.write_text(json.dumps(payload), encoding="utf-8")
            mismatch = subprocess.run(
                [
                    str(orro),
                    "engine-lock",
                    "--home",
                    str(home),
                    "--check",
                    str(mismatched),
                    "--json",
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(mismatch.returncode, 1)
            mismatch_payload = json.loads(mismatch.stdout)
            self.assertFalse(mismatch_payload["locked"])
            self.assertEqual(
                mismatch_payload["error"]["code"],
                "ERR_ORRO_ENGINE_LOCK_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
