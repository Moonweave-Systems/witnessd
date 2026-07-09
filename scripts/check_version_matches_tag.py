#!/usr/bin/env python3
"""Guard against setup.py's version falling behind the latest git tag."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_PY = ROOT / "setup.py"


def read_setup_version(setup_py_text: str) -> str | None:
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', setup_py_text)
    return match.group(1) if match else None


def latest_tag() -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def not_behind(setup_version: str, tag: str) -> bool:
    """Return True when setup.py version is not behind the tag.

    The guard catches a release tag that outran the package version (the original
    bug: tag v2.2.0 while setup.py stayed 0.0.0). A version equal to or ahead of
    the latest tag is normal (e.g. bumped while preparing the next release), so
    only a strictly-behind version fails.
    """
    return _version_tuple(setup_version) >= _version_tuple(tag.lstrip("v"))


def self_test() -> None:
    assert not_behind("2.2.0", "v2.2.0") is True
    assert not_behind("2.2.1", "v2.2.0") is True  # ahead while preparing next release
    assert not_behind("0.0.0", "v2.2.0") is False  # the original bug: behind the tag
    assert not_behind("2.1.0", "v2.2.0") is False
    print("self-test: equal/ahead pass, behind detected -- OK")


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0

    if not SETUP_PY.exists():
        print(f"note: {SETUP_PY} not found, skipping version-tag check")
        return 0

    setup_version = read_setup_version(SETUP_PY.read_text())
    if setup_version is None:
        print("note: could not find version= in setup.py, skipping version-tag check")
        return 0

    tag = latest_tag()
    if tag is None:
        print("note: no reachable git tag (git unavailable or shallow checkout), skipping version-tag check")
        return 0

    if not not_behind(setup_version, tag):
        print(
            f"ERROR: setup.py version ({setup_version!r}) is behind the latest tag "
            f"({tag!r}); bump setup.py to at least {tag.lstrip('v')!r}",
            file=sys.stderr,
        )
        return 1

    print(f"version-tag coherence OK: setup.py={setup_version!r} not behind tag={tag!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
