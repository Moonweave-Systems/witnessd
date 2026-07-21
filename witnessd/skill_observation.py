"""Redaction-safe skill observation from raw provider events."""

from __future__ import annotations

import json
import re
from typing import Any

_SKILL_PATH_RE = re.compile(
    r"(?:^|[\s'\"`])(?:[A-Za-z0-9_./~$:-]*/)?(?:\.agents/skills|skills)/"
    r"(?P<skill>[^/\s'\"`]+)/"
)
_DECLARED_SKILL_RE = re.compile(
    r"\busing\s+the\s+[`'\"]?(?P<skill>[A-Za-z0-9_.-]+)[`'\"]?\s+skill\b",
    re.IGNORECASE,
)


def observed_skills_from_raw_events(raw_events: bytes | list[Any], adapter: str) -> list[str]:
    """Return sorted unique skill identifiers observed in raw provider events."""

    if adapter not in {"codex", "codex-local"}:
        return []
    observed: set[str] = set()
    for event in _events(raw_events):
        for text in _strings(event):
            observed.update(match.group("skill") for match in _SKILL_PATH_RE.finditer(text))
            observed.update(
                match.group("skill") for match in _DECLARED_SKILL_RE.finditer(text)
            )
    return sorted(observed)


def _events(raw_events: bytes | list[Any]) -> list[Any]:
    if isinstance(raw_events, list):
        return raw_events
    events: list[Any] = []
    for line in raw_events.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            events.append(line.decode("utf-8", errors="replace"))
    return events


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_strings(item))
        return strings
    return []
