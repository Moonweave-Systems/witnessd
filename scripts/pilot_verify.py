#!/usr/bin/env python3
"""Wrap Depone verification commands into an external-pilot transcript."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_check(name: str, command: str, cwd: str | None) -> dict[str, Any]:
    argv = shlex.split(command)
    result = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "name": name,
        "command": argv,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def build_transcript(
    *,
    deployment_id: str,
    production_command: str,
    canary_command: str,
    cwd: str | None = None,
) -> dict[str, Any]:
    results = [
        _run_check("production_bundle", production_command, cwd),
        _run_check("canary_bundle", canary_command, cwd),
    ]
    return {
        "kind": "depone-verification-transcript",
        "schema_version": "1.0",
        "rollout_stage": "external-team-pilot",
        "deployment_id": deployment_id,
        "verifier": "depone",
        "created_at": _utc_now(),
        "all_passed": all(item["exit_code"] == 0 for item in results),
        "results": results,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--production-command", required=True)
    parser.add_argument("--canary-command", required=True)
    parser.add_argument("--cwd", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    transcript = build_transcript(
        deployment_id=args.deployment_id,
        production_command=args.production_command,
        canary_command=args.canary_command,
        cwd=args.cwd,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(transcript, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"depone_verification: {out_path}")
    print(f"depone_verification_sha256: {_sha256(out_path)}")
    return 0 if transcript["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
