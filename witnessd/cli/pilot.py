from __future__ import annotations

import argparse
import sys
from pathlib import Path

def _cmd_pilot_init(args: argparse.Namespace) -> int:
    from witnessd.pilot import write_deployment_record

    deployed_runtime = bool(args.deployed_runtime and args.not_dogfood and args.not_ci)
    record_path = write_deployment_record(
        operator=args.operator,
        team_scope=args.team_scope,
        out_dir=args.out,
        deployed_runtime=deployed_runtime,
        local_dogfood=not deployed_runtime,
        ci_only=not deployed_runtime,
        repo_root=args.deployment_root,
    )
    print(f"deployment_record: {record_path}")
    return 0


def _cmd_pilot_close(args: argparse.Namespace) -> int:
    from witnessd.pilot import close_deployment_record

    digest = close_deployment_record(args.record)
    print(f"deployment_record_sha256: {digest}")
    return 0


def _cmd_pilot_rotation_record(args: argparse.Namespace) -> int:
    from witnessd.pilot import write_rotation_record

    record_path = write_rotation_record(
        archive_path=args.archive,
        out_dir=args.out,
        retired_key_id=args.retired_key_id,
    )
    print(f"rotation_record: {record_path}")
    return 0


def _cmd_pilot_canary(args: argparse.Namespace) -> int:
    from witnessd.pilot import emit_canary_bundle

    bundle_path = emit_canary_bundle(keys_dir=args.keys_dir, out_dir=args.out)
    print(f"canary_bundle: {bundle_path}")
    return 0


def _cmd_pilot_archive_evidence(args: argparse.Namespace) -> int:
    from witnessd.pilot import record_archive_evidence

    artifacts: dict[str, str | Path] = {}
    for entry in args.artifact:
        if "=" not in entry:
            print("ERR_ARCHIVE_ARTIFACT_FORMAT", file=sys.stderr)
            return 2
        evidence_id, path = entry.split("=", 1)
        if not evidence_id or not path:
            print("ERR_ARCHIVE_ARTIFACT_FORMAT", file=sys.stderr)
            return 2
        artifacts[evidence_id] = path
    try:
        archive_path = record_archive_evidence(
            archive_path=args.archive,
            artifacts=artifacts,
            out_path=args.out,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"operator_key_archive: {archive_path}")
    return 0
