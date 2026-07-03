"""External-team pilot tooling.

This module records pilot evidence scaffolding only. It does not verify Depone
claims and does not open production gates.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEPLOYMENT_KIND = "witnessd-external-team-pilot-deployment"
ROTATION_RECORD_KIND = "witnessd-operator-key-rotation-record"
TRANSCRIPT_KIND = "depone-verification-transcript"
ROLLOUT_STAGE = "external-team-pilot"
SCHEMA_VERSION = "1.0"
DEPLOYMENT_RECORD_NAME = "deployment-record.json"
ROTATION_RECORD_NAME = "rotation-record.json"
CANARY_KIND = "operator-key-rotation-canary"
CANARY_RECORD_NAME = "operator-key-canary.json"
CANARY_BUNDLE_NAME = "operator-key-canary-bundle.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        value = result.stdout.strip()
        if value:
            return value
    return "0" * 12


def _deployment_id(operator: str, team_scope: str, started_at: str) -> str:
    seed = json.dumps(
        {"operator": operator, "team_scope": team_scope, "started_at": started_at},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "pilot-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def write_deployment_record(
    *,
    operator: str,
    team_scope: str,
    out_dir: str | Path,
    deployed_runtime: bool = False,
    local_dogfood: bool = True,
    ci_only: bool = True,
    repo_root: str | Path | None = None,
) -> Path:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    started_at = utc_now()
    record = {
        "kind": DEPLOYMENT_KIND,
        "schema_version": SCHEMA_VERSION,
        "rollout_stage": ROLLOUT_STAGE,
        "deployment_id": _deployment_id(operator, team_scope, started_at),
        "operator": operator,
        "team_scope": team_scope,
        "started_at": started_at,
        "ended_at": None,
        "witnessd_git_sha": _git_sha(root),
        "deployed_runtime": deployed_runtime,
        "local_dogfood": local_dogfood,
        "ci_only": ci_only,
    }
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    record_path = out_path / DEPLOYMENT_RECORD_NAME
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record_path


def close_deployment_record(record_path: str | Path) -> str:
    path = Path(record_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("ended_at") is None:
        record["ended_at"] = utc_now()
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sha256_file(path)


def _current_archive_key(archive: dict[str, Any]) -> dict[str, Any]:
    keys = archive.get("keys")
    if not isinstance(keys, list):
        raise ValueError("operator key archive keys must be a list")
    current = [key for key in keys if isinstance(key, dict) and key.get("status") == "current"]
    if len(current) != 1:
        raise ValueError("operator key archive must contain exactly one current key")
    return current[0]


def write_rotation_record(
    *,
    archive_path: str | Path,
    out_dir: str | Path,
    retired_key_id: str = "witnessd-operator",
) -> Path:
    from witnessd.signing import DEFAULT_OPERATOR_KEY_ID

    archive = json.loads(Path(archive_path).read_text(encoding="utf-8"))
    current_key = _current_archive_key(archive)
    current_key_id = current_key.get("key_id")
    if current_key_id != DEFAULT_OPERATOR_KEY_ID:
        raise ValueError("current key must match witnessd runtime default")
    if retired_key_id == current_key_id:
        raise ValueError("retired key must differ from current key")
    canary_bundle_path = current_key.get("bundle_path")
    if not isinstance(canary_bundle_path, str) or not canary_bundle_path:
        raise ValueError("current key must include bundle_path")
    record = {
        "kind": ROTATION_RECORD_KIND,
        "schema_version": SCHEMA_VERSION,
        "rollout_stage": ROLLOUT_STAGE,
        "retired_key_id": retired_key_id,
        "current_key_id": current_key_id,
        "rotated_to": current_key_id,
        "canary_bundle_path": canary_bundle_path,
    }
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    record_path = out_path / ROTATION_RECORD_NAME
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record_path


def emit_canary_bundle(*, keys_dir: str | Path, out_dir: str | Path) -> Path:
    from witnessd.signing import DEFAULT_OPERATOR_KEY_ID
    from witnessd.substrate import build_bundle

    keys = Path(keys_dir)
    private_key = keys / "operator-ed25519.pem"
    public_key = keys / "operator-ed25519.pub.pem"
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    canary_path = out_path / CANARY_RECORD_NAME
    canary = {
        "kind": CANARY_KIND,
        "schema_version": SCHEMA_VERSION,
        "rollout_stage": ROLLOUT_STAGE,
        "created_at": utc_now(),
        "key_id": DEFAULT_OPERATOR_KEY_ID,
        "public_key_path": str(public_key),
    }
    canary_path.write_text(json.dumps(canary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "kind": CANARY_KIND,
        "assurance": "A1-local-observed",
        "decision": CANARY_KIND,
        "evidence_mode": "contemporaneous",
        "epoch_seconds": 300,
        "monotonic_counter": 1,
    }
    bundle = build_bundle(
        manifest,
        {"operator-key-rotation-canary": str(canary_path)},
        str(private_key),
        str(public_key),
        key_id=DEFAULT_OPERATOR_KEY_ID,
    )
    bundle_path = out_path / CANARY_BUNDLE_NAME
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bundle_path


def _relative_to_cwd(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def record_archive_evidence(
    *,
    archive_path: str | Path,
    artifacts: dict[str, str | Path],
    out_path: str | Path | None = None,
) -> Path:
    archive_file = Path(archive_path)
    archive = json.loads(archive_file.read_text(encoding="utf-8"))
    gate = archive["production_gate"]
    required = gate["required_evidence"]
    by_id = {item["id"]: item for item in required}
    for evidence_id, artifact in artifacts.items():
        if evidence_id not in by_id:
            raise ValueError(f"unknown evidence id: {evidence_id}")
        artifact_path = Path(artifact)
        item = by_id[evidence_id]
        item["status"] = "recorded"
        item["artifact_path"] = _relative_to_cwd(artifact_path)
        item["artifact_sha256"] = sha256_file(artifact_path)
    target = Path(out_path) if out_path is not None else archive_file
    target.write_text(json.dumps(archive, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = write_deployment_record(
            operator="operator@example.invalid",
            team_scope="external-team:self-test",
            out_dir=tmp,
            repo_root=Path(__file__).resolve().parents[1],
        )
        body = json.loads(path.read_text(encoding="utf-8"))
        if not body["local_dogfood"] or not body["ci_only"]:
            raise AssertionError("pilot deployment record must default to dogfood/CI")
        rotation_path = write_rotation_record(
            archive_path=Path(__file__).resolve().parents[1]
            / "fixtures/key-rotation/operator-key-archive.json",
            out_dir=Path(tmp) / "rotation",
        )
        rotation = json.loads(rotation_path.read_text(encoding="utf-8"))
        if rotation["current_key_id"] == rotation["retired_key_id"]:
            raise AssertionError("pilot rotation record must rotate to a distinct key")
    print("witnessd pilot --self-test: pass")
