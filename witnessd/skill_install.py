"""Install the packaged ORRO session skill into the operator's Claude home."""

from __future__ import annotations

import importlib.metadata
import os
import re
import tempfile
from pathlib import Path

from witnessd.distribution import WITNESSD_PACKAGE_VERSION_FALLBACK


SKILL_RELATIVE_PATH = Path(".claude") / "skills" / "orro" / "SKILL.md"
SKILL_VERSION_RE = re.compile(r"^witnessd_version:\s*(\S+)\s*$", re.MULTILINE)
SKILL_GENERATED_MARKER = "witnessd_generated: true"


def package_version() -> str:
    try:
        return importlib.metadata.version("witnessd")
    except importlib.metadata.PackageNotFoundError:
        return WITNESSD_PACKAGE_VERSION_FALLBACK


def installed_skill_path() -> Path:
    return Path.home() / SKILL_RELATIVE_PATH


def _skill_source() -> str:
    try:
        packaged = importlib.metadata.distribution("witnessd").locate_file(
            "share/witnessd/SKILL.md"
        )
        if packaged.is_file():
            return packaged.read_text(encoding="utf-8")
    except (importlib.metadata.PackageNotFoundError, OSError, UnicodeDecodeError):
        pass
    source = Path(__file__).resolve().parents[1] / "SKILL.md"
    if not source.is_file():
        raise FileNotFoundError("packaged ORRO skill source is unavailable")
    return source.read_text(encoding="utf-8")


def skill_text() -> str:
    version = package_version()
    source = _skill_source()
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


def install_skill(*, force: bool = False) -> dict[str, str]:
    path = installed_skill_path()
    existed = path.exists()
    if existed and not force and not _is_owned(path):
        raise FileExistsError(
            "installed skill exists but witnessd did not write it; refusing to overwrite "
            "an operator-edited file without --force"
        )
    content = skill_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return {"action": "refreshed" if existed else "installed", "path": str(path)}


def inspect_skill(path: Path | None = None) -> dict[str, object]:
    path = path or installed_skill_path()
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
