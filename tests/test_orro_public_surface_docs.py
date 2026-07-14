"""Regression guards for the hand-maintained ORRO public-surface tables."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TABLE_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
ORRO_USAGE = re.compile(r"usage: orro \[-h\] \{([^}]+)\}")


def _documented_commands(path: Path) -> set[str]:
    """Return normalized ORRO command families from a public-surface table."""

    commands: set[str] = set()
    for entry in TABLE_ROW.findall(path.read_text(encoding="utf-8")):
        parts = entry.split()
        if not parts:
            continue
        if parts[0] == "orro":
            command = parts[1] if len(parts) > 1 else ""
        else:
            command = parts[0]
        commands.add(f"orro {command}".rstrip())
    return commands


def _cli_commands() -> set[str]:
    """Read the public ORRO subcommand names from the actual CLI help."""

    result = subprocess.run(
        [sys.executable, "-m", "orro", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    match = ORRO_USAGE.search(result.stdout)
    if match is None:
        raise AssertionError(f"ORRO help did not expose a command list: {result.stdout}")
    return {f"orro {name}" for name in match.group(1).split(",")}


class OrroPublicSurfaceDocsTest(unittest.TestCase):
    def test_public_surface_tables_match_skill_and_real_cli(self) -> None:
        canonical = _documented_commands(ROOT / "SKILL.md")
        for filename in ("CLAUDE.md", "README.md"):
            actual = _documented_commands(ROOT / filename)
            self.assertEqual(
                actual,
                canonical,
                f"{filename} ORRO public-command table drifted from SKILL.md",
            )

        self.assertIn("orro setup", canonical)
        cli_commands = _cli_commands()
        missing_cli_commands = canonical - {"orro"} - cli_commands
        self.assertTrue(
            canonical - {"orro"} <= cli_commands,
            f"documentation lists commands missing from the ORRO CLI: "
            f"{sorted(missing_cli_commands)}",
        )


if __name__ == "__main__":
    unittest.main()
