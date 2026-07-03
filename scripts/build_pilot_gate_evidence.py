#!/usr/bin/env python3
"""Build stable external-team-pilot gate evidence without opening the gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEPONE_ROOT = Path("/home/ubuntu/moonweave/depone")
DEFAULT_PILOT_ROOT = Path("/home/ubuntu/pilot-2026-07-03")
DEFAULT_ARCHIVE = ROOT / "fixtures/key-rotation/operator-key-archive.json"
DEFAULT_STABLE_DIR = ROOT / "fixtures/external-team-pilot"
CANARY_BUNDLE = ROOT / "fixtures/key-rotation/operator-key-canary-bundle.json"
CANARY_PUBLIC_KEY = ROOT / "fixtures/key-rotation/keys/operator-2026q3.pub"


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _env_with_depone(depone_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(depone_root)
        if not existing
        else str(depone_root) + os.pathsep + existing
    )
    return env


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _stable_transcript_matches(existing: Path, candidate: Path) -> bool:
    if not existing.is_file():
        return False
    try:
        existing_body = _load_json(existing)
        candidate_body = _load_json(candidate)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    existing_body.pop("created_at", None)
    candidate_body.pop("created_at", None)
    return existing_body == candidate_body and candidate_body.get("all_passed") is True


def _copy_if_changed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        return
    shutil.copyfile(src, dst)


def _default_production_command(pilot_root: Path) -> str:
    production_ev = pilot_root / "production-ev"
    return (
        "uv run python3 -m depone team-ledger "
        f"--ledger {production_ev / 'team-ledger.json'} "
        f"--base-dir {production_ev} --json"
    )


def _default_canary_command() -> str:
    return (
        "uv run python3 -m depone agent-fabric-verify-signature "
        f"--bundle {CANARY_BUNDLE} --public-key {CANARY_PUBLIC_KEY}"
    )


def build_pilot_gate_evidence(args: argparse.Namespace) -> int:
    pilot_root = Path(args.pilot_root)
    archive = Path(args.archive)
    stable_dir = Path(args.stable_dir)
    depone_root = Path(args.depone_root)
    deployment_record = Path(args.deployment_record) if args.deployment_record else (
        pilot_root / "deployment/deployment-record.json"
    )
    production_command = args.production_command or _default_production_command(pilot_root)
    canary_command = args.canary_command or _default_canary_command()
    verify_cwd = Path(args.verify_cwd) if args.verify_cwd else depone_root
    env = _env_with_depone(depone_root)

    close = _run(
        [
            sys.executable,
            "-m",
            "witnessd",
            "pilot",
            "close",
            "--record",
            str(deployment_record),
        ],
        cwd=ROOT,
        env=env,
    )
    if close.returncode != 0:
        return close.returncode

    rotation_out = pilot_root / "archive"
    rotation = _run(
        [
            sys.executable,
            "-m",
            "witnessd",
            "pilot",
            "rotation-record",
            "--archive",
            str(archive),
            "--out",
            str(rotation_out),
        ],
        cwd=ROOT,
        env=env,
    )
    if rotation.returncode != 0:
        return rotation.returncode
    rotation_record = rotation_out / "rotation-record.json"

    deployment_id = str(_load_json(deployment_record)["deployment_id"])
    stable_transcript = stable_dir / "depone-verification-transcript.json"
    with tempfile.TemporaryDirectory(prefix="witnessd-pilot-verify-") as tmp:
        candidate_transcript = Path(tmp) / "depone-verification-transcript.json"
        verify = _run(
            [
                sys.executable,
                str(ROOT / "scripts/pilot_verify.py"),
                "--deployment-id",
                deployment_id,
                "--out",
                str(candidate_transcript),
                "--production-command",
                production_command,
                "--canary-command",
                canary_command,
                "--cwd",
                str(verify_cwd),
            ],
            cwd=ROOT,
            env=env,
        )
        if verify.returncode != 0:
            return verify.returncode
        if not _load_json(candidate_transcript).get("all_passed"):
            print("depone verification transcript did not pass", file=sys.stderr)
            return 1

        _copy_if_changed(deployment_record, stable_dir / "deployment-record.json")
        _copy_if_changed(rotation_record, stable_dir / "rotation-record.json")
        if not _stable_transcript_matches(stable_transcript, candidate_transcript):
            _copy_if_changed(candidate_transcript, stable_transcript)

    archive_update = _run(
        [
            sys.executable,
            "-m",
            "witnessd",
            "pilot",
            "archive-evidence",
            "--archive",
            str(archive),
            "--artifact",
            f"deployment_record={stable_dir / 'deployment-record.json'}",
            "--artifact",
            f"rotated_key_archive={stable_dir / 'rotation-record.json'}",
            "--artifact",
            f"canary_bundle={CANARY_BUNDLE}",
            "--artifact",
            f"depone_verification={stable_transcript}",
        ],
        cwd=ROOT,
        env=env,
    )
    if archive_update.returncode != 0:
        return archive_update.returncode

    for path in [
        stable_dir / "deployment-record.json",
        stable_dir / "rotation-record.json",
        CANARY_BUNDLE,
        stable_transcript,
    ]:
        print(f"artifact: {path.relative_to(ROOT)} sha256={_sha256(path)}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-root", default=str(DEFAULT_PILOT_ROOT))
    parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--stable-dir", default=str(DEFAULT_STABLE_DIR))
    parser.add_argument("--depone-root", default=str(DEFAULT_DEPONE_ROOT))
    parser.add_argument("--deployment-record", default=None)
    parser.add_argument("--production-command", default=None)
    parser.add_argument("--canary-command", default=None)
    parser.add_argument("--verify-cwd", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return build_pilot_gate_evidence(_build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
