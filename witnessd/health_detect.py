from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

_VERSION = re.compile(r"(?<!\d)(\d+(?:\.\d+)+(?:[-+._a-zA-Z0-9]*)?)(?!\d)")
_PRE_COMMIT_CONFIGS = (".pre-commit-config.yaml", ".pre-commit-config.yml")


def detect_health_gates(repo: Path) -> list[dict[str, str | None]]:
    """Return configured health gates in stable tool order."""

    pyproject = _read_text(repo / "pyproject.toml")
    package_json = _read_text(repo / "package.json")
    pre_commit = "\n".join(_read_text(repo / name) for name in _PRE_COMMIT_CONFIGS)
    gates: list[dict[str, str | None]] = []
    if (
        _has_tool_section(pyproject, "black")
        or _pyproject_mentions_dependency(pyproject, "black")
        or _mentions_tool((pre_commit,), "black")
    ):
        gates.append(_gate("format", "black", "black --check --quiet ."))
    if (
        _has_tool_section(pyproject, "ruff")
        or (repo / "ruff.toml").is_file()
        or _pyproject_mentions_dependency(pyproject, "ruff")
        or _mentions_tool((pre_commit,), "ruff")
    ):
        gates.append(_gate("lint", "ruff", "ruff check ."))
    if (
        _has_tool_section(pyproject, "mypy")
        or (repo / "mypy.ini").is_file()
        or _pyproject_mentions_dependency(pyproject, "mypy")
        or _mentions_tool((pre_commit,), "mypy")
    ):
        gates.append(_gate("type", "mypy", "mypy ."))
    if _has_glob(repo, ".eslintrc*") or _mentions_tool(
        (package_json, pre_commit), "eslint"
    ):
        gates.append(_gate("lint", "eslint", "npx --no-install eslint ."))
    if _has_glob(repo, ".prettierrc*") or _mentions_tool(
        (package_json, pre_commit), "prettier"
    ):
        gates.append(
            _gate(
                "format",
                "prettier",
                "npx --no-install prettier --check .",
            )
        )
    if (repo / "go.mod").is_file():
        gates.append(
            _gate(
                "format",
                "gofmt",
                "sh -c 'test -z \"$(gofmt -l .)\"'",
            )
        )
    return gates


def safe_fixer_commands(gates: Iterable[dict[str, object]]) -> list[str]:
    fixers = {
        "black": "black .",
        "ruff": "ruff check --fix .",
        "prettier": "npx --no-install prettier --write .",
        "gofmt": "gofmt -w .",
    }
    tools = {str(gate.get("tool")) for gate in gates}
    return [fixers[tool] for tool in fixers if tool in tools]


def resolve_tool_version(tool: str) -> str:
    argv = _version_argv(tool)
    executable = shutil.which(argv[0])
    if executable is None:
        return "unresolved"
    argv[0] = executable
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unresolved"
    if result.returncode != 0:
        return "unresolved"
    match = _VERSION.search(f"{result.stdout}\n{result.stderr}")
    return match.group(1) if match else "unresolved"


def _gate(gate: str, tool: str, command: str) -> dict[str, str]:
    return {
        "gate": gate,
        "tool": tool,
        "command": command,
        "version": resolve_tool_version(tool),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _has_tool_section(text: str, tool: str) -> bool:
    pattern = re.compile(rf"^\s*\[tool\.{re.escape(tool)}(?:\.|\])", re.MULTILINE)
    return pattern.search(text) is not None


def _mentions_tool(texts: Iterable[str], tool: str) -> bool:
    pattern = _tool_pattern(tool)
    return any(pattern.search(text) is not None for text in texts)


def _pyproject_mentions_dependency(text: str, tool: str) -> bool:
    pattern = _tool_pattern(tool)
    dependency_value = False
    dependency_section = False
    for line in text.splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"\[([^]]+)\]", stripped)
        if header:
            section = header.group(1).lower()
            dependency_section = (
                "dependenc" in section or section == "dependency-groups"
            )
            dependency_value = False
            continue
        if dependency_section and pattern.search(line):
            return True
        if re.match(
            r"^(?:dependencies|optional-dependencies|dev-dependencies)\s*=", stripped
        ):
            if pattern.search(line):
                return True
            dependency_value = "[" in line and "]" not in line
            continue
        if dependency_value:
            if pattern.search(line):
                return True
            if "]" in line:
                dependency_value = False
    return False


def _tool_pattern(tool: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(tool)}(?![A-Za-z0-9_-])",
        re.IGNORECASE,
    )


def _has_glob(repo: Path, pattern: str) -> bool:
    try:
        return any(repo.glob(pattern))
    except OSError:
        return False


def _version_argv(tool: str) -> list[str]:
    if tool in {"eslint", "prettier"}:
        return ["npx", "--no-install", tool, "--version"]
    if tool == "gofmt":
        return ["go", "version"]
    return [tool, "--version"]
