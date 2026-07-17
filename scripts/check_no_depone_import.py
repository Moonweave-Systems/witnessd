#!/usr/bin/env python3
"""Fail when the witnessd runtime package imports Depone."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def _depone_imports(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[tuple[int, str]] = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        imports_depone = False
        if isinstance(node, ast.Import):
            imports_depone = any(
                alias.name == "depone" or alias.name.startswith("depone.")
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports_depone = node.level == 0 and (
                module == "depone" or module.startswith("depone.")
            )
        if imports_depone:
            line = lines[node.lineno - 1].strip()
            findings.append((node.lineno, line))
    return sorted(findings)


def check_runtime_package(root: Path) -> list[str]:
    package = root / "witnessd"
    if not package.is_dir():
        raise FileNotFoundError(f"runtime package not found: {package}")

    findings: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        for line_number, line in _depone_imports(path):
            findings.append(f"{relative}:{line_number}: forbidden import: {line}")
    return findings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="statically reject Depone imports in witnessd/**/*.py"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root containing the witnessd package",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve(strict=False)
    try:
        findings = check_runtime_package(root)
    except (FileNotFoundError, OSError, SyntaxError) as exc:
        print(f"no-depone-import check failed: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("witnessd runtime must not import Depone:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
