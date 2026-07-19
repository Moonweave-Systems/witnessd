from __future__ import annotations

import argparse
import json

from witnessd.cli._output import _structured_error


def _emit_blocker(error: dict[str, object]) -> int:
    print(
        json.dumps(
            {
                "kind": "orro-companion-result",
                "decision": "blocked",
                "error": error,
            },
            sort_keys=True,
        )
    )
    return 2


def _cmd_orro_check(args: argparse.Namespace) -> int:
    checks = list(getattr(args, "check", None) or [])
    if not checks:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_NO_CHECKS_DECLARED",
                message="orro check requires at least one --check command",
                reason="checks define what 'verified' means and cannot be inferred",
                required_input_or_grant="--check '<cmd>' (repeatable)",
                next_command="python3 -m orro check --check '<cmd>' --repo <repo>",
            )
        )
    raise NotImplementedError
