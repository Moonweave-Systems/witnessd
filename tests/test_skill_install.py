from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.skill_install import package_version


class SkillInstallTests(unittest.TestCase):
    def _run(self, argv: list[str], *, home: Path) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        with patch.dict(os.environ, {"HOME": str(home)}, clear=False), redirect_stdout(stdout):
            code = main(argv)
        return code, json.loads(stdout.getvalue())

    def test_install_writes_package_versioned_skill_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, payload = self._run(["orro", "skill", "install", "--json"], home=home)

            self.assertEqual(code, 0)
            skill_path = home / ".claude" / "skills" / "orro" / "SKILL.md"
            self.assertEqual(payload["path"], str(skill_path))
            self.assertEqual(payload["action"], "installed")
            text = skill_path.read_text(encoding="utf-8")
            self.assertIn(f"witnessd_version: {package_version()}", text)
            self.assertIn("witnessd_generated: true", text)
            self.assertNotIn("orro report", text)
            self.assertNotIn("orro next", text)
            self.assertNotIn("orro sketch", text)
            self.assertNotIn("orro trace", text)

            code, payload = self._run(["orro", "skill", "install", "--json"], home=home)
            self.assertEqual(code, 0)
            self.assertEqual(payload["action"], "refreshed")

    def test_install_refuses_unowned_file_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill_path = home / ".claude" / "skills" / "orro" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text("operator document\n", encoding="utf-8")

            code, payload = self._run(["orro", "skill", "install", "--json"], home=home)
            self.assertEqual(code, 2)
            self.assertIn("did not write", str(payload["error"]["message"]))
            self.assertIn("--force", str(payload["error"]["next_command"]))
            self.assertEqual(skill_path.read_text(encoding="utf-8"), "operator document\n")

    def test_doctor_distinguishes_missing_stale_and_removed_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch(
                "witnessd.cli.verify.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "depone doctor: pass\n", ""),
            ), patch(
                "witnessd.preflight.probe_adapter_capability",
                return_value={"decision": "pass"},
            ):
                code, payload = self._run(
                    ["orro", "doctor", "--home", str(home), "--json"], home=home
                )
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(code, 1)
            self.assertEqual(checks["installed_skill"]["status"], "missing")
            self.assertIn("orro skill install", checks["installed_skill"]["remediation"]["command"])

            skill_path = home / ".claude" / "skills" / "orro" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nwitnessd_version: 0.0.0\nwitnessd_generated: true\n---\n\nrun `orro report`\n",
                encoding="utf-8",
            )
            with patch(
                "witnessd.cli.verify.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "depone doctor: pass\n", ""),
            ), patch(
                "witnessd.preflight.probe_adapter_capability",
                return_value={"decision": "pass"},
            ):
                code, payload = self._run(
                    ["orro", "doctor", "--home", str(home), "--json"], home=home
                )
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(code, 1)
            self.assertEqual(checks["installed_skill"]["status"], "stale")
            self.assertFalse(checks["installed_skill"]["version_matches"])
            self.assertIn("report", checks["installed_skill"]["removed_commands"])
            self.assertIn("orro skill install", checks["installed_skill"]["remediation"]["command"])


if __name__ == "__main__":
    unittest.main()
