"""Adapter capability preflight for W4 lanes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from witnessd.codex_capability import (
    build_codex_local_capability,
    validate_codex_local_capability,
)


ERR_OPENCODE_HEADLESS_NOOP_KNOWN_NONFUNCTIONAL = (
    "ERR_OPENCODE_HEADLESS_NOOP_KNOWN_NONFUNCTIONAL"
)


class PreflightError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _boundary() -> dict[str, bool]:
    return {
        "launches_live_model": False,
        "executes_coding_task": False,
        "captures_capability_only": True,
        "raises_assurance": False,
    }


def _local_adapter_capability(
    adapter: str,
    *,
    binary: str,
    repo: str,
) -> dict[str, object]:
    binary_path = shutil.which(binary)
    blocked_reasons = [] if binary_path is not None else [f"{adapter} binary not found"]
    return {
        "kind": "witnessd-adapter-capability",
        "schema_version": "0.1",
        "decision": "blocked" if blocked_reasons else "pass",
        "blocked_reasons": blocked_reasons,
        "adapter": {
            "id": adapter,
            "binary": binary,
            "binary_path": binary_path,
        },
        "repo": {"path": str(Path(repo).resolve(strict=False))},
        "boundary": _boundary(),
    }


def probe_adapter_capability(
    adapter: str,
    *,
    repo: str,
    codex_binary: str = "codex",
    claude_binary: str = "claude",
    agy_binary: str = "agy",
    gemini_binary: str = "gemini",
    opencode_binary: str = "opencode",
    require_ready: bool = False,
    **kwargs: object,
) -> dict[str, object]:
    if adapter == "codex":
        receipt = build_codex_local_capability(
            repo=Path(repo),
            codex_binary=codex_binary,
            **kwargs,
        )
    elif adapter == "claude":
        receipt = _local_adapter_capability(
            "claude", binary=claude_binary, repo=repo
        )
    elif adapter == "agy":
        receipt = _local_adapter_capability(
            "agy", binary=agy_binary, repo=repo
        )
    elif adapter == "gemini":
        receipt = _local_adapter_capability(
            "gemini", binary=gemini_binary, repo=repo
        )
    elif adapter == "opencode":
        receipt = _local_adapter_capability(
            "opencode", binary=opencode_binary, repo=repo
        )
        if receipt["adapter"]["binary_path"] is not None:
            receipt["decision"] = "blocked"
            receipt["blocked_reasons"] = [
                f"{ERR_OPENCODE_HEADLESS_NOOP_KNOWN_NONFUNCTIONAL}: "
                "opencode run is known to exit 0 without events or file edits "
                "when launched headlessly through the adapter"
            ]
    else:
        raise PreflightError(
            "ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE",
            f"unknown adapter: {adapter}",
        )

    if require_ready and receipt.get("decision") != "pass":
        raise PreflightError(
            "ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE",
            "; ".join(str(item) for item in receipt.get("blocked_reasons", []))
            or f"{adapter} adapter unavailable",
        )
    return receipt


def _self_test() -> None:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        receipt = probe_adapter_capability(
            "codex",
            repo=repo,
            codex_binary="definitely-missing-codex-for-witnessd-self-test",
        )
        errors = validate_codex_local_capability(receipt)
        if errors:
            raise AssertionError(errors)
        if receipt["decision"] != "blocked":
            raise AssertionError("missing codex binary must block")
