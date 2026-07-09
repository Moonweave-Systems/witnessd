#!/usr/bin/env python3
"""Guard against setup.py's version and the latest git tag silently diverging."""
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


def compare(setup_version: str, tag: str) -> bool:
    """Return True when setup_version matches tag (leading 'v' stripped)."""
    return setup_version == tag.lstrip("v")


def self_test() -> None:
    assert compare("2.2.0", "v2.2.0") is True
    assert compare("2.2.0", "2.2.0") is True
    assert compare("0.0.0", "v2.2.0") is False
    print("self-test: matching case passes, mismatching case detected -- OK")


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

    if not compare(setup_version, tag):
        print(
            f"ERROR: setup.py version ({setup_version!r}) does not match latest tag "
            f"({tag!r}, stripped {tag.lstrip('v')!r})",
            file=sys.stderr,
        )
        return 1

    print(f"version-tag coherence OK: setup.py={setup_version!r} matches tag={tag!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
