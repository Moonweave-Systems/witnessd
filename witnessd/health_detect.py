from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

_VERSION = re.compile(r"(?<!\d)(\d+(?:\.\d+)+(?:[-+._a-zA-Z0-9]*)?)(?!\d)")
_PRE_COMMIT_CONFIGS = (".pre-commit-config.yaml", ".pre-commit-config.yml")
_BOOTSTRAP_CONFIG_SECTIONS = (
    ("tool.black", "black", "[tool.black]\nline-length = 88\n"),
    ("tool.ruff", "ruff", '[tool.ruff]\nselect = ["E", "F", "I", "ERA"]\n'),
    (
        "tool.ruff.lint.mccabe",
        "ruff.lint.mccabe",
        "[tool.ruff.lint.mccabe]\nmax-complexity = 10\n",
    ),
)


def seed_missing_gate_config(repo: Path) -> dict[str, list[str]]:
    """Append bootstrap config only for tool sections that are not present."""

    pyproject_path = repo / "pyproject.toml"
    pyproject = (
        pyproject_path.read_text(encoding="utf-8") if pyproject_path.exists() else ""
    )
    written: list[str] = []
    present: list[str] = []
    blocks: list[str] = []
    for label, tool, block in _BOOTSTRAP_CONFIG_SECTIONS:
        if _has_tool_section(pyproject, tool):
            present.append(label)
        else:
            written.append(label)
            blocks.append(block)
    if blocks:
        with pyproject_path.open("a", encoding="utf-8") as stream:
            for block in blocks:
                stream.write("\n" + block)
    return {"written": written, "present": present}


def ensure_health_profile(repo: Path) -> tuple[list[dict[str, str]], bool]:
    """Write the bootstrap profile once, preserving any existing user profile."""

    existing = _read_health_profile(repo)
    if existing is not None:
        return existing, False
    profile = _bootstrap_health_profile()
    _write_health_profile(repo, profile)
    return profile, True


def promote_health_gates(repo: Path, gate_names: Iterable[str]) -> list[dict[str, str]]:
    """Promote known profile gates to blocking enforcement."""

    profile = _read_health_profile(repo)
    if profile is None:
        raise FileNotFoundError(repo / ".orro" / "health.json")
    requested = list(dict.fromkeys(gate_names))
    known = {gate["gate"] for gate in profile}
    unknown = [gate for gate in requested if gate not in known]
    if unknown:
        raise ValueError("unknown health gate: " + ", ".join(unknown))
    promoted = [dict(gate) for gate in profile]
    for gate in promoted:
        if gate["gate"] in requested:
            gate["enforcement"] = "block"
    _write_health_profile(repo, promoted)
    return promoted


def _bootstrap_health_profile() -> list[dict[str, str]]:
    return [
        {
            "gate": "format",
            "tool": "black",
            "command": "black --check --quiet .",
            "enforcement": "block",
        },
        {
            "gate": "lint",
            "tool": "ruff",
            "command": "ruff check .",
            "enforcement": "advisory",
        },
        {
            "gate": "complexity",
            "tool": "ruff-c901",
            "command": "ruff check --select C901 .",
            "enforcement": "advisory",
        },
    ]


def _health_profile_path(repo: Path) -> Path:
    return repo / ".orro" / "health.json"


def _read_health_profile(repo: Path) -> list[dict[str, str]] | None:
    path = _health_profile_path(repo)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(".orro/health.json must contain a list of gates")
    profile: list[dict[str, str]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f".orro/health.json gate {index} must be an object")
        values: dict[str, str] = {}
        for key in ("gate", "tool", "command", "enforcement"):
            value = item.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f".orro/health.json gate {index}.{key} must be a non-empty string"
                )
            values[key] = value
        if values["enforcement"] not in {"block", "advisory"}:
            raise ValueError(
                f".orro/health.json gate {index}.enforcement must be block or advisory"
            )
        profile.append(values)
    return profile


def _write_health_profile(repo: Path, profile: list[dict[str, str]]) -> None:
    path = _health_profile_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def detect_health_gates(repo: Path) -> list[dict[str, str | None]]:
    """Return configured health gates in stable tool order."""

    profile = _read_health_profile(repo)
    if profile is not None:
        return [
            {
                **gate,
                "version": resolve_tool_version(gate["tool"]),
            }
            for gate in profile
        ]

    pyproject = _read_text(repo / "pyproject.toml")
    package_json = _read_text(repo / "package.json")
    pre_commit = "\n".join(_read_text(repo / name) for name in _PRE_COMMIT_CONFIGS)
    gates: list[dict[str, str | None]] = []
    if (
        _has_tool_section(pyproject, "black")
        or _pyproject_mentions_dependency(pyproject, "black")
        or _mentions_tool((pre_commit,), "black")
    ):
        gates.append(_gate("format", "black", "black --check --quiet .", "block"))
    if (
        _has_tool_section(pyproject, "ruff")
        or (repo / "ruff.toml").is_file()
        or _pyproject_mentions_dependency(pyproject, "ruff")
        or _mentions_tool((pre_commit,), "ruff")
    ):
        gates.append(_gate("lint", "ruff", "ruff check .", "block"))
    if (
        _has_tool_section(pyproject, "mypy")
        or (repo / "mypy.ini").is_file()
        or _pyproject_mentions_dependency(pyproject, "mypy")
        or _mentions_tool((pre_commit,), "mypy")
    ):
        gates.append(_gate("type", "mypy", "mypy .", "block"))
    if _has_glob(repo, ".eslintrc*") or _mentions_tool(
        (package_json, pre_commit), "eslint"
    ):
        gates.append(_gate("lint", "eslint", "npx --no-install eslint .", "block"))
    if _has_glob(repo, ".prettierrc*") or _mentions_tool(
        (package_json, pre_commit), "prettier"
    ):
        gates.append(
            _gate(
                "format",
                "prettier",
                "npx --no-install prettier --check .",
                "block",
            )
        )
    if (repo / "go.mod").is_file():
        gates.append(
            _gate(
                "format",
                "gofmt",
                "sh -c 'test -z \"$(gofmt -l .)\"'",
                "block",
            )
        )
    if _ruff_is_configured(repo, pyproject, pre_commit) and _has_complexity_config(
        pyproject, _read_text(repo / "ruff.toml")
    ):
        gates.append(
            _gate(
                "complexity",
                "ruff-c901",
                "ruff check --select C901 .",
                "advisory",
            )
        )
    if (repo / ".importlinter").is_file() or _has_tool_section(
        pyproject, "importlinter"
    ):
        gates.append(
            _gate(
                "architecture",
                "import-linter",
                "lint-imports",
                "block",
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


def _gate(gate: str, tool: str, command: str, enforcement: str) -> dict[str, str]:
    return {
        "gate": gate,
        "tool": tool,
        "command": command,
        "version": resolve_tool_version(tool),
        "enforcement": enforcement,
    }


def _ruff_is_configured(repo: Path, pyproject: str, pre_commit: str) -> bool:
    return (
        _has_tool_section(pyproject, "ruff")
        or (repo / "ruff.toml").is_file()
        or _pyproject_mentions_dependency(pyproject, "ruff")
        or _mentions_tool((pre_commit,), "ruff")
    )


def _has_complexity_config(pyproject: str, ruff_toml: str) -> bool:
    return any(
        re.search(
            r"^\s*\[(?:tool\.ruff\.)?lint\.mccabe\]",
            text,
            re.MULTILINE,
        )
        or re.search(r"^\s*max-complexity\s*=", text, re.MULTILINE)
        for text in (pyproject, ruff_toml)
    )


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
    if tool == "ruff-c901":
        return ["ruff", "--version"]
    if tool == "import-linter":
        return ["lint-imports", "--version"]
    return [tool, "--version"]
