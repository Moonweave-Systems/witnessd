"""Install the packaged ORRO session skills into the operator's Claude home."""

from __future__ import annotations

import importlib.metadata
import os
import re
import sys
import tempfile
from pathlib import Path

from witnessd.distribution import WITNESSD_PACKAGE_VERSION_FALLBACK


SKILL_RELATIVE_PATHS = {
    "orro": Path(".claude") / "skills" / "orro" / "SKILL.md",
    "orro-inspect": Path(".claude") / "skills" / "orro-inspect" / "SKILL.md",
}
SKILL_RELATIVE_PATH = SKILL_RELATIVE_PATHS["orro"]
SKILL_VERSION_RE = re.compile(r"^witnessd_version:\s*(\S+)\s*$", re.MULTILINE)
SKILL_GENERATED_MARKER = "witnessd_generated: true"


def package_version() -> str:
    try:
        return importlib.metadata.version("witnessd")
    except importlib.metadata.PackageNotFoundError:
        return WITNESSD_PACKAGE_VERSION_FALLBACK


def installed_skill_path(name: str = "orro") -> Path:
    return Path.home() / SKILL_RELATIVE_PATHS[name]


def _skill_source(name: str = "orro") -> str:
    source_name = "SKILL.md" if name == "orro" else "SKILL_INSPECT.md"
    candidates: list[Path] = []
    try:
        candidates.append(importlib.metadata.distribution("witnessd").locate_file(
            f"share/witnessd/{source_name}"
        ))
    except (importlib.metadata.PackageNotFoundError, OSError, UnicodeDecodeError):
        pass
    for prefix in dict.fromkeys((sys.prefix, sys.base_prefix)):
        candidates.append(Path(prefix) / "share" / "witnessd" / source_name)
    candidates.append(Path(__file__).resolve().parents[1] / source_name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"packaged ORRO skill source is unavailable; tried: {tried}"
    )


def skill_text(name: str = "orro") -> str:
    version = package_version()
    source = _skill_source(name)
    lines = source.splitlines()
    if lines[:1] != ["---"]:
        raise ValueError("packaged ORRO skill is missing front matter")
    end = next((index for index, line in enumerate(lines[1:], 1) if line == "---"), None)
    if end is None:
        raise ValueError("packaged ORRO skill has unterminated front matter")
    front_matter = lines[1:end]
    front_matter = [line for line in front_matter if not line.startswith("witnessd_version:")]
    front_matter = [line for line in front_matter if not line.startswith("witnessd_generated:")]
    rendered = ["---", f"witnessd_version: {version}", SKILL_GENERATED_MARKER, *front_matter, "---", *lines[end + 1 :]]
    return "\n".join(rendered) + "\n"


def _is_owned(path: Path) -> bool:
    try:
        return SKILL_GENERATED_MARKER in path.read_text(encoding="utf-8").split("---", 2)[1]
    except (OSError, UnicodeDecodeError, IndexError):
        return False


def install_skill(*, force: bool = False) -> dict[str, object]:
    installed = []
    for name in SKILL_RELATIVE_PATHS:
        path = installed_skill_path(name)
        existed = path.exists()
        if existed and not force and not _is_owned(path):
            raise FileExistsError(
                f"installed skill {name!r} exists but witnessd did not write it; refusing "
                "to overwrite an operator-edited file without --force"
            )
        content = skill_text(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        installed.append(
            {"name": name, "action": "refreshed" if existed else "installed", "path": str(path)}
        )
    return {
        "action": "refreshed" if all(item["action"] == "refreshed" for item in installed) else "installed",
        "path": str(installed[0]["path"]),
        "skills": installed,
    }


def inspect_skill(name: str = "orro", path: Path | None = None) -> dict[str, object]:
    path = path or installed_skill_path(name)
    if not path.is_file():
        return {"status": "missing", "path": str(path), "version": None, "version_matches": False, "removed_commands": []}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "unreadable", "path": str(path), "version": None, "version_matches": False, "removed_commands": [], "detail": str(exc)}
    version_match = SKILL_VERSION_RE.search(text)
    version = version_match.group(1) if version_match else None
    running_version = package_version()
    from witnessd.__main__ import ORRO_REMOVED_ALIASES

    removed_commands = sorted(
        command for command in ORRO_REMOVED_ALIASES if f"orro {command}" in text
    )
    healthy = version == running_version and not removed_commands
    return {
        "status": "pass" if healthy else "stale",
        "path": str(path),
        "version": version,
        "running_version": running_version,
        "version_matches": version == running_version,
        "removed_commands": removed_commands,
    }
