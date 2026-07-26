from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import (
    ORRO_COMMAND_MAP,
    ORRO_RISKY_COMMAND_TARGETS,
    main,
)
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
            inspect_path = home / ".claude" / "skills" / "orro-inspect" / "SKILL.md"
            self.assertEqual(payload["path"], str(skill_path))
            self.assertEqual(payload["action"], "installed")
            self.assertEqual({skill["name"] for skill in payload["skills"]}, {"orro", "orro-inspect"})
            text = skill_path.read_text(encoding="utf-8")
            self.assertIn(f"witnessd_version: {package_version()}", text)
            self.assertIn("witnessd_generated: true", text)
            self.assertNotIn("orro report", text)
            self.assertNotIn("orro next", text)
            self.assertNotIn("orro sketch", text)
            self.assertNotIn("orro trace", text)
            inspect_text = inspect_path.read_text(encoding="utf-8")
            self.assertIn(f"witnessd_version: {package_version()}", inspect_text)

            code, payload = self._run(["orro", "skill", "install", "--json"], home=home)
            self.assertEqual(code, 0)
            self.assertEqual(payload["action"], "refreshed")

    def test_public_verbs_are_partitioned_between_skill_sources(self) -> None:
        root = Path(__file__).resolve().parents[1]
        texts = {
            "orro": (root / "SKILL.md").read_text(encoding="utf-8"),
            "orro-inspect": (root / "SKILL_INSPECT.md").read_text(encoding="utf-8"),
        }
        risky_verbs = {
            verb
            for verb, target in ORRO_COMMAND_MAP.items()
            if target in ORRO_RISKY_COMMAND_TARGETS
        }
        inspect_verbs = set(ORRO_COMMAND_MAP) - risky_verbs
        for verb in ORRO_COMMAND_MAP:
            matches = [
                name
                for name, text in texts.items()
                if re.search(rf"\borro\s+{re.escape(verb)}\b", text)
            ]
            self.assertEqual(matches, ["orro-inspect"] if verb in inspect_verbs else ["orro"], verb)

    def test_inspect_skill_contains_no_risky_public_command(self) -> None:
        from witnessd.__main__ import ORRO_COMMAND_MAP

        inspect_text = (Path(__file__).resolve().parents[1] / "SKILL_INSPECT.md").read_text(
            encoding="utf-8"
        )
        risky_verbs = {
            verb for verb, target in ORRO_COMMAND_MAP.items() if target in ORRO_RISKY_COMMAND_TARGETS
        }
        for verb in risky_verbs:
            self.assertIsNone(re.search(rf"\borro\s+{re.escape(verb)}\b", inspect_text), verb)

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
            self.assertEqual(checks["installed_skill_inspect"]["status"], "missing")
            self.assertIn("orro skill install", checks["installed_skill"]["remediation"]["command"])

            skill_path = home / ".claude" / "skills" / "orro" / "SKILL.md"
            inspect_path = home / ".claude" / "skills" / "orro-inspect" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nwitnessd_version: 0.0.0\nwitnessd_generated: true\n---\n\nrun `orro report`\n",
                encoding="utf-8",
            )
            inspect_path.parent.mkdir(parents=True)
            inspect_path.write_text(
                f"---\nwitnessd_version: {package_version()}\nwitnessd_generated: true\n---\n",
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
            self.assertEqual(checks["installed_skill_inspect"]["status"], "pass")
            self.assertFalse(checks["installed_skill"]["version_matches"])
            self.assertIn("report", checks["installed_skill"]["removed_commands"])
            self.assertIn("orro skill install", checks["installed_skill"]["remediation"]["command"])

            inspect_path.write_text(
                f"---\nwitnessd_version: {package_version()}\nwitnessd_generated: true\n---\n"
                "run `orro report`\n",
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
            self.assertEqual(checks["installed_skill_inspect"]["status"], "stale")
            self.assertIn("report", checks["installed_skill_inspect"]["removed_commands"])


if __name__ == "__main__":
    unittest.main()
