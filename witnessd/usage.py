"""Shared parsing for adapter-reported token usage."""

from __future__ import annotations

import json
from pathlib import Path


def usage_from_transcript(path: Path) -> dict[str, int | None]:
    """Sum top-level JSONL usage counters, or return unknown when absent."""
    input_tokens = 0
    output_tokens = 0
    usage_seen = False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        observed_input = usage.get("input_tokens")
        observed_output = usage.get("output_tokens")
        if isinstance(observed_input, int):
            input_tokens += observed_input
            usage_seen = True
        if isinstance(observed_output, int):
            output_tokens += observed_output
            usage_seen = True
    return {
        "input": input_tokens if usage_seen else None,
        "output": output_tokens if usage_seen else None,
    }
