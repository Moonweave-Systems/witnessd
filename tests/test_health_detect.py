from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.health_detect import (
    detect_health_gates,
    ensure_health_profile,
    promote_health_gates,
    safe_fixer_commands,
    seed_missing_gate_config,
)


def _write_tool(bin_dir: Path, name: str, version_output: str) -> None:
    path = bin_dir / name
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{version_output}'\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _write_npx(bin_dir: Path) -> None:
    path = bin_dir / "npx"
    path.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  eslint) printf '%s\\n' 'v9.1.0' ;;\n"
        "  prettier) printf '%s\\n' '3.3.3' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class HealthDetectionTest(unittest.TestCase):
    def test_seed_missing_gate_config_appends_only_absent_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = (
                "[project]\n" 'name = "kept"\n\n' "[tool.ruff]\n" 'select = ["F401"]\n'
            )
            pyproject = root / "pyproject.toml"
            pyproject.write_text(original, encoding="utf-8")

            report = seed_missing_gate_config(root)
            seeded = pyproject.read_text(encoding="utf-8")

            self.assertEqual(
                report,
                {
                    "written": ["tool.black", "tool.ruff.lint.mccabe"],
                    "present": ["tool.ruff"],
                },
            )
            self.assertTrue(seeded.startswith(original))
            self.assertEqual(seeded[: len(original)], original)
            self.assertIn("\n[tool.black]\nline-length = 88\n", seeded)
            self.assertIn("\n[tool.ruff.lint.mccabe]\nmax-complexity = 10\n", seeded)
            self.assertEqual(seeded.count("[tool.ruff]"), 1)

    def test_seed_missing_gate_config_creates_complete_default_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            report = seed_missing_gate_config(root)
            seeded = (root / "pyproject.toml").read_text(encoding="utf-8")

            self.assertEqual(
                report,
                {
                    "written": [
                        "tool.black",
                        "tool.ruff",
                        "tool.ruff.lint.mccabe",
                    ],
                    "present": [],
                },
            )
            self.assertIn("[tool.black]\nline-length = 88", seeded)
            self.assertIn('[tool.ruff]\nselect = ["E", "F", "I"]', seeded)
            self.assertIn("[tool.ruff.lint.mccabe]\nmax-complexity = 10", seeded)

    def test_health_profile_round_trips_and_wins_over_auto_detect_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.ruff]\n[tool.mypy]\n", encoding="utf-8"
            )

            profile, written = ensure_health_profile(root)
            profile_again, written_again = ensure_health_profile(root)

            self.assertTrue(written)
            self.assertFalse(written_again)
            self.assertEqual(profile_again, profile)
            self.assertEqual(
                json.loads(
                    (root / ".orro" / "health.json").read_text(encoding="utf-8")
                ),
                profile,
            )
            gates = detect_health_gates(root)
            self.assertEqual(
                [(gate["gate"], gate["tool"], gate["enforcement"]) for gate in gates],
                [
                    ("format", "black", "block"),
                    ("lint", "ruff", "advisory"),
                    ("complexity", "ruff-c901", "advisory"),
                ],
            )
            self.assertNotIn("mypy", {gate["tool"] for gate in gates})

    def test_promote_health_gates_changes_only_requested_advisory_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, _ = ensure_health_profile(root)

            after = promote_health_gates(root, ["lint"])

            self.assertEqual(
                next(gate for gate in after if gate["gate"] == "lint")["enforcement"],
                "block",
            )
            unchanged = [gate for gate in before if gate["gate"] != "lint"]
            self.assertEqual(
                [gate for gate in after if gate["gate"] != "lint"], unchanged
            )

    def test_pyproject_ruff_and_mypy_yield_ordered_gates_with_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.ruff]\nline-length = 88\n\n[tool.mypy]\nstrict = true\n",
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_tool(bin_dir, "ruff", "ruff 0.6.9")
            _write_tool(bin_dir, "mypy", "mypy 1.11.2 (compiled: yes)")

            with patch.dict(os.environ, {"PATH": str(bin_dir)}):
                gates = detect_health_gates(root)

            self.assertEqual(
                gates,
                [
                    {
                        "gate": "lint",
                        "tool": "ruff",
                        "command": "ruff check .",
                        "version": "0.6.9",
                        "enforcement": "block",
                    },
                    {
                        "gate": "type",
                        "tool": "mypy",
                        "command": "mypy .",
                        "version": "1.11.2",
                        "enforcement": "block",
                    },
                ],
            )

    def test_supported_config_signals_yield_the_full_ordered_gate_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                '[project]\ndependencies = ["black==24.10.0"]\n',
                encoding="utf-8",
            )
            (root / "ruff.toml").write_text("line-length = 88\n", encoding="utf-8")
            (root / "mypy.ini").write_text("[mypy]\nstrict = true\n", encoding="utf-8")
            (root / ".eslintrc.json").write_text("{}\n", encoding="utf-8")
            (root / ".prettierrc").write_text("{}\n", encoding="utf-8")
            (root / "go.mod").write_text(
                "module example.invalid/health\n", encoding="utf-8"
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_tool(bin_dir, "black", "black, 24.10.0")
            _write_tool(bin_dir, "ruff", "ruff 0.6.9")
            _write_tool(bin_dir, "mypy", "mypy 1.11.2")
            _write_tool(bin_dir, "go", "go version go1.23.1 linux/amd64")
            _write_npx(bin_dir)

            with patch.dict(os.environ, {"PATH": str(bin_dir)}):
                gates = detect_health_gates(root)

            self.assertEqual(
                [
                    (
                        gate["gate"],
                        gate["tool"],
                        gate["command"],
                        gate["version"],
                        gate["enforcement"],
                    )
                    for gate in gates
                ],
                [
                    ("format", "black", "black --check --quiet .", "24.10.0", "block"),
                    ("lint", "ruff", "ruff check .", "0.6.9", "block"),
                    ("type", "mypy", "mypy .", "1.11.2", "block"),
                    (
                        "lint",
                        "eslint",
                        "npx --no-install eslint .",
                        "9.1.0",
                        "block",
                    ),
                    (
                        "format",
                        "prettier",
                        "npx --no-install prettier --check .",
                        "3.3.3",
                        "block",
                    ),
                    (
                        "format",
                        "gofmt",
                        "sh -c 'test -z \"$(gofmt -l .)\"'",
                        "1.23.1",
                        "block",
                    ),
                ],
            )

    def test_configured_tool_without_an_executable_is_not_silently_skipped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mypy.ini").write_text("[mypy]\nstrict = true\n", encoding="utf-8")

            with patch.dict(os.environ, {"PATH": str(root / "empty-bin")}):
                gates = detect_health_gates(root)

            self.assertEqual(
                gates,
                [
                    {
                        "gate": "type",
                        "tool": "mypy",
                        "command": "mypy .",
                        "version": "unresolved",
                        "enforcement": "block",
                    }
                ],
            )

    def test_incidental_pyproject_prose_does_not_invent_a_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\n"
                'name = "ruff-guide"\n'
                'description = "why black and mypy matter"\n',
                encoding="utf-8",
            )

            self.assertEqual(detect_health_gates(root), [])

    def test_complexity_and_architecture_gates_use_declared_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.ruff]\n"
                "[tool.ruff.lint.mccabe]\n"
                "max-complexity = 3\n"
                "[tool.importlinter]\n"
                "root_package = 'pkg'\n",
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_tool(bin_dir, "ruff", "ruff 0.6.9")
            _write_tool(bin_dir, "lint-imports", "import-linter 2.1")

            with patch.dict(os.environ, {"PATH": str(bin_dir)}):
                gates = detect_health_gates(root)

            self.assertEqual(
                gates,
                [
                    {
                        "gate": "lint",
                        "tool": "ruff",
                        "command": "ruff check .",
                        "version": "0.6.9",
                        "enforcement": "block",
                    },
                    {
                        "gate": "complexity",
                        "tool": "ruff-c901",
                        "command": "ruff check --select C901 .",
                        "version": "0.6.9",
                        "enforcement": "advisory",
                    },
                    {
                        "gate": "architecture",
                        "tool": "import-linter",
                        "command": "lint-imports",
                        "version": "2.1",
                        "enforcement": "block",
                    },
                ],
            )

    def test_safe_fixers_include_only_the_locked_semantics_preserving_subset(
        self,
    ) -> None:
        gates = [
            {"tool": tool}
            for tool in ("black", "ruff", "mypy", "eslint", "prettier", "gofmt")
        ]

        self.assertEqual(
            safe_fixer_commands(gates),
            [
                "black .",
                "ruff check --fix .",
                "npx --no-install prettier --write .",
                "gofmt -w .",
            ],
        )


if __name__ == "__main__":
    unittest.main()
