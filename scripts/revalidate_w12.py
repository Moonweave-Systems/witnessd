"""Strict W12 real-A2 revalidation.

This script has no demonstration branch. It passes only when W12 fixture bytes
contain a Depone-valid A2 manifest whose isolation facts independently establish
the observer-launched uid boundary. Phase A may import the helper assertions in
tests; the default CLI requires the Phase B fixture to exist.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.isolation import (
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
    verify_isolation_boundary,
)

from witnessd.canonical import canonical_hash

FIX = REPO_ROOT / "fixtures" / "w12"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_strict_real_a2(manifest: dict[str, Any]) -> None:
    errors = validate_capture_manifest(manifest)
    _require(errors == [], f"W12 capture manifest must pass Depone validation: {errors!r}")
    _require(
        manifest.get("assurance") == "A2-isolated-observed",
        f"W12 assurance must be A2-isolated-observed, got {manifest.get('assurance')!r}",
    )
    isolation = manifest.get("isolation")
    _require(isinstance(isolation, dict), "W12 A2 manifest must include isolation facts")
    verified = verify_isolation_boundary(isolation)
    _require(
        verified.get("boundary") is True,
        f"W12 isolation facts must establish a boundary: {verified!r}",
    )
    _require(
        verified.get("model") == UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
        "W12 must use the observer-launched uid isolation model",
    )
    _require(
        verified.get("runner_uid") != verified.get("observer_uid"),
        "W12 runner and observer uids must differ",
    )
    _require(verified.get("runner_uid") != 0, "W12 root runner uid is forbidden")
    _require(
        verified.get("observer_dir_writable_by_runner") is False,
        "W12 observer_dir must be proven not writable by the runner",
    )
    _require(
        verified.get("observer_launched") is True,
        "W12 runner must be observer-launched",
    )


def assert_runner_writable_observer_dir_blocks(manifest: dict[str, Any]) -> None:
    forged = copy.deepcopy(manifest)
    isolation = dict(forged["isolation"])
    isolation["observer_dir_writable_by_runner"] = True
    forged["isolation"] = isolation
    forged["isolation_hash"] = canonical_hash(isolation)
    errors = validate_capture_manifest(forged)
    _require(
        any("does not establish a privilege boundary" in error for error in errors),
        f"runner-writable observer_dir must be blocked by Depone, got {errors!r}",
    )
    _require(
        verify_isolation_boundary(isolation).get("boundary") is False,
        "runner-writable observer_dir must fail the isolation verifier",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(FIX / "capture-manifest.json"),
        help="W12 real A2 capture manifest path",
    )
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(
            f"W12 revalidate: missing real A2 fixture {manifest_path}; "
            "run Phase B after operator-approved host setup",
            file=sys.stderr,
        )
        return 2
    manifest = _load_json(manifest_path)
    assert_strict_real_a2(manifest)
    assert_runner_writable_observer_dir_blocks(manifest)
    print("W12 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
