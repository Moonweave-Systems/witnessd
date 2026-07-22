from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.health_detect import detect_health_gates, safe_fixer_commands


def _write_tool(bin_dir: Path, name: str, version_output: str) -> None:
    path = bin_dir / name
    path.write_text(
        "#!/bin/sh\n" f"printf '%s\\n' '{version_output}'\n",
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
                    },
                    {
                        "gate": "type",
                        "tool": "mypy",
                        "command": "mypy .",
                        "version": "1.11.2",
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
                    (gate["gate"], gate["tool"], gate["command"], gate["version"])
                    for gate in gates
                ],
                [
                    ("format", "black", "black --check --quiet .", "24.10.0"),
                    ("lint", "ruff", "ruff check .", "0.6.9"),
                    ("type", "mypy", "mypy .", "1.11.2"),
                    ("lint", "eslint", "npx --no-install eslint .", "9.1.0"),
                    (
                        "format",
                        "prettier",
                        "npx --no-install prettier --check .",
                        "3.3.3",
                    ),
                    (
                        "format",
                        "gofmt",
                        "sh -c 'test -z \"$(gofmt -l .)\"'",
                        "1.23.1",
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
