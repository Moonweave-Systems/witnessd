"""Human-declared intent helpers for ORRO decision surfaces.

Declared intent is invoker-provided advisory context. Its reference is
convenience-grade tamper evidence only; it is not proof, approval, or assurance.
"""

from __future__ import annotations

import errno
import json
import re
from pathlib import Path
from typing import Any

from witnessd.cli._output import _hash_file


ERR_ORRO_INTENT_READ_FAILED = "ERR_ORRO_INTENT_READ_FAILED"
ERR_ORRO_INTENT_INVALID = "ERR_ORRO_INTENT_INVALID"
INTENT_ALIGNMENT_NOTE = (
    "absence of drift warnings is lexical-screening absence only, not evidence of alignment"
)
INTENT_SCHEMA = "{intent: str, non_goals?: [str], constraints?: [str]}"
_READ_LIMIT_BYTES = 262_144
_STOPWORDS = {
    "another",
    "because",
    "from",
    "have",
    "into",
    "must",
    "only",
    "should",
    "than",
    "that",
    "their",
    "this",
    "with",
    "without",
}
_TOKEN_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*", re.UNICODE)


def _intent_error(code: str, detail: str | None = None) -> Exception:
    from witnessd.orro_advisory import OrroAdvisoryError

    message = (
        "--intent expects a path to a JSON file, not inline text. "
        f"Schema: {INTENT_SCHEMA}"
    )
    if detail:
        message = f"{message}. {detail}"
    return OrroAdvisoryError(code, message)


def read_declared_intent(path: Path) -> dict[str, Any]:
    """Read and validate a bounded human-declared intent JSON object."""

    try:
        resolved_path = path.resolve(strict=False)
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise _intent_error(ERR_ORRO_INTENT_READ_FAILED) from exc
        raise _intent_error(ERR_ORRO_INTENT_READ_FAILED, f"Cannot resolve {path}: {exc}") from exc
    if not resolved_path.exists():
        raise _intent_error(ERR_ORRO_INTENT_READ_FAILED)
    try:
        if resolved_path.stat().st_size > _READ_LIMIT_BYTES:
            raise _intent_error(
                ERR_ORRO_INTENT_INVALID,
                "Declared intent exceeds the 256 KiB read limit",
            )
        value = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        from witnessd.orro_advisory import OrroAdvisoryError

        if isinstance(exc, OrroAdvisoryError):
            raise
        if isinstance(exc, OSError) and exc.errno == errno.ENAMETOOLONG:
            raise _intent_error(ERR_ORRO_INTENT_READ_FAILED) from exc
        raise _intent_error(ERR_ORRO_INTENT_READ_FAILED, f"Cannot read {resolved_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise _intent_error(ERR_ORRO_INTENT_INVALID, "Declared intent must be a JSON object")
    intent = value.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise _intent_error(ERR_ORRO_INTENT_INVALID, "intent must be a non-empty string")
    for field in ("non_goals", "constraints"):
        entries = value.get(field)
        if entries is not None and (
            not isinstance(entries, list) or not all(isinstance(item, str) for item in entries)
        ):
            raise _intent_error(ERR_ORRO_INTENT_INVALID, f"{field} must be a list of strings")
    return value


def declared_intent_ref(path: Path) -> dict[str, object]:
    """Reference the exact declared-intent file bytes supplied by the invoker."""

    return {"path": str(path), "sha256": _hash_file(path), "declared": True}


def screen_intent_drift(text: str, non_goals: list[str]) -> list[dict[str, object]]:
    """Return deterministic lexical warnings without gating or raising."""

    try:
        if not isinstance(text, str) or not isinstance(non_goals, list):
            return []
        normalized_text = text.casefold()
        warnings: list[dict[str, object]] = []
        for non_goal in non_goals:
            if not isinstance(non_goal, str):
                continue
            seen: set[str] = set()
            for token in _TOKEN_RE.findall(non_goal.casefold()):
                if len(token) < 4 or token in _STOPWORDS or token in seen:
                    continue
                seen.add(token)
                if token in normalized_text:
                    warnings.append(
                        {
                            "non_goal": non_goal,
                            "matched_token": token,
                            "matched_in": text,
                            "method": "lexical-screening",
                            "can_change_evidence_verdict": False,
                        }
                    )
        return warnings
    except Exception:  # noqa: BLE001 - advisory screening must never gate a command
        return []
