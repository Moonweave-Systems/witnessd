from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

def _read_runlog(path: str) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _depone_subprocess_env(home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if home is None:
        return env
    from witnessd.distribution import validate_depone_pin

    provision = validate_depone_pin(home)
    depone_root = Path(str(provision["depone"]["root"])).resolve(strict=False)
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(depone_root)
        if not current_pythonpath
        else f"{depone_root}{os.pathsep}{current_pythonpath}"
    )
    return env


def _run_depone_json(command: list[str], *, env: dict[str, str]) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "depone", *command, "--json"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if not completed.stdout.strip():
        return completed.returncode, {
            "error": {
                "code": "ERR_ORRO_DEPONE_DELEGATION_FAILED",
                "message": completed.stderr.strip()
                or "Depone verifier produced no JSON output",
            }
        }
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:
        return completed.returncode, {
            "error": {
                "code": "ERR_ORRO_DEPONE_DELEGATION_INVALID_JSON",
                "message": completed.stdout,
            }
        }


def _structured_error(
    *,
    code: str,
    message: str,
    reason: str | None = None,
    required_input_or_grant: str | None = None,
    next_command: str | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if reason is not None:
        error["reason"] = reason
    if required_input_or_grant is not None:
        error["required_input_or_grant"] = required_input_or_grant
    if next_command is not None:
        error["next_command"] = next_command
    if extra:
        error.update(extra)
    return error


def _emit_orro_error(
    args: argparse.Namespace,
    *,
    code: str,
    message: str,
    reason: str | None = None,
    required_input_or_grant: str | None = None,
    next_command: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    error = _structured_error(
        code=code,
        message=message,
        reason=reason,
        required_input_or_grant=required_input_or_grant,
        next_command=next_command,
        extra=extra,
    )
    if getattr(args, "json", False):
        print(json.dumps({"error": error}, sort_keys=True))
        return
    print(code, file=sys.stderr)
    if next_command is not None:
        print(f"{message} Next: {next_command}", file=sys.stderr)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _json_or_text(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
