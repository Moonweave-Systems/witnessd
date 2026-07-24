"""Distribution and local Depone pinning helpers for W18."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ERR_WITNESSD_DEPONE_PIN_MISMATCH = "ERR_WITNESSD_DEPONE_PIN_MISMATCH"
ERR_WITNESSD_DEPONE_PIN_MISSING = "ERR_WITNESSD_DEPONE_PIN_MISSING"
ERR_WITNESSD_DEPONE_ROOT_INVALID = "ERR_WITNESSD_DEPONE_ROOT_INVALID"
ERR_WITNESSD_INIT_NETWORK_REQUIRED = "ERR_WITNESSD_INIT_NETWORK_REQUIRED"
ERR_WITNESSD_DEPONE_PROVISION_FAILED = "ERR_WITNESSD_DEPONE_PROVISION_FAILED"
ERR_WITNESSD_DEPONE_VERIFY_FAILED = "ERR_WITNESSD_DEPONE_VERIFY_FAILED"
ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING = "ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING"
ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISMATCH = "ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISMATCH"
ERR_ORRO_ENGINE_LOCK_LOAD_FAILED = "ERR_ORRO_ENGINE_LOCK_LOAD_FAILED"
ERR_ORRO_ENGINE_LOCK_INVALID = "ERR_ORRO_ENGINE_LOCK_INVALID"
ERR_ORRO_ENGINE_LOCK_MISMATCH = "ERR_ORRO_ENGINE_LOCK_MISMATCH"
ERR_ORRO_ENGINE_LOCK_WITNESSD_IDENTITY = "ERR_ORRO_ENGINE_LOCK_WITNESSD_IDENTITY"
ERR_ORRO_SETUP_DEPONE_PIN_MISMATCH = "ERR_ORRO_SETUP_DEPONE_PIN_MISMATCH"

PROVISION_KIND = "witnessd-depone-provision"
PROVISION_SCHEMA_VERSION = "0.1"
ORRO_ENGINE_LOCK_KIND = "orro-engine-lock"
ORRO_ENGINE_LOCK_SCHEMA_VERSION = "1.0"
DEFAULT_DEPONE_REPOSITORY = "https://github.com/Moonweave-Systems/Depone.git"
DEFAULT_DEPONE_REF = "f067a05299f755fe6b3c4b86aace2aa8822cc711"
WITNESSD_PACKAGE_VERSION_FALLBACK = "2.28.0"


class ProvisionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InitConfig:
    home: Path
    witnessd_root: Path
    depone_root: Path | None = None
    network_allowed: bool = False
    depone_repository: str | None = None
    depone_ref: str | None = None
    team_path: Path | None = None


def init_witnessd_home(config: InitConfig) -> dict[str, str]:
    home = config.home.expanduser()
    if not home.is_absolute():
        home = home.resolve(strict=False)
    home.mkdir(parents=True, exist_ok=True)
    keys_dir = home / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(keys_dir, 0o700)

    private_placeholder = keys_dir / "operator-private-key.placeholder"
    if not private_placeholder.exists():
        private_placeholder.write_text(
            "placeholder: generated run keys are created per evidence run\n",
            encoding="utf-8",
        )
    os.chmod(private_placeholder, 0o600)

    depone_root, depone_source, network_used = _resolve_depone_root(config)
    witnessd_root = config.witnessd_root.resolve(strict=False)
    provision = _build_provision(
        witnessd_root=witnessd_root,
        depone_root=depone_root,
        network_used=network_used,
        depone_source=depone_source,
        team_ref=_build_team_ref(config.team_path),
    )
    config_payload = {
        "kind": "witnessd-config",
        "schema_version": "0.1",
        "home": str(home),
        "keys_dir": str(keys_dir),
        "depone_provision": "provision.json",
    }
    _write_json(home / "config.json", config_payload)
    _write_json(home / "provision.json", provision)
    return {
        "home": str(home),
        "config": str(home / "config.json"),
        "provision": str(home / "provision.json"),
        "keys_dir": str(keys_dir),
    }


def validate_depone_pin(home: Path) -> dict[str, Any]:
    provision = _load_depone_provision(home)
    depone = provision["depone"]
    depone_root = Path(str(depone["root"])).resolve(strict=False)
    current_commit = _git_commit(depone_root)
    if current_commit != str(depone["commit"]):
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISMATCH)
    return provision


def classify_depone_pin_state(
    home: Path, *, depone_ref: str | None = None
) -> dict[str, Any]:
    """Describe the local Depone pin state without weakening enforcement."""

    try:
        provision = _load_depone_provision(home)
    except ProvisionError as exc:
        return {"state": "missing", "code": exc.code}

    depone = provision["depone"]
    depone_root = Path(str(depone["root"])).resolve(strict=False)
    recorded_commit = str(depone["commit"])
    result: dict[str, Any] = {
        "state": "mismatch",
        "code": ERR_WITNESSD_DEPONE_PIN_MISMATCH,
        "depone_root": str(depone_root),
        "recorded_commit": recorded_commit,
        "current_commit": None,
        "expected_commit": None,
    }
    try:
        current_commit = _git_commit(depone_root)
    except ProvisionError as exc:
        result["code"] = exc.code
        return result
    result["current_commit"] = current_commit
    if current_commit == recorded_commit:
        result["state"] = "ok"
        result.pop("code")
        return result

    expected_ref = (
        depone_ref or os.environ.get("WITNESSD_DEPONE_REF") or DEFAULT_DEPONE_REF
    )
    try:
        expected_commit = _git_commit_for_ref(depone_root, expected_ref)
    except ProvisionError:
        return result
    result["expected_commit"] = expected_commit
    if current_commit != expected_commit:
        return result

    try:
        resolved_recorded_commit = _git_commit_for_ref(depone_root, recorded_commit)
    except ProvisionError:
        return result
    if resolved_recorded_commit != recorded_commit:
        return result
    if not _git_commit_is_ancestor(
        depone_root, ancestor=recorded_commit, descendant=current_commit
    ):
        return result
    result["state"] = "stale-upgrade"
    return result


def _load_depone_provision(home: Path) -> dict[str, Any]:
    provision_path = home.resolve(strict=False) / "provision.json"
    if not provision_path.is_file():
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING)
    try:
        provision = json.loads(provision_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING) from exc
    if not isinstance(provision, dict):
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING)
    if provision.get("kind") != PROVISION_KIND:
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING)
    depone = provision.get("depone")
    if not isinstance(depone, dict):
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING)
    root = depone.get("root")
    recorded_commit = depone.get("commit")
    if not isinstance(root, str) or not isinstance(recorded_commit, str):
        raise ProvisionError(ERR_WITNESSD_DEPONE_PIN_MISSING)
    return provision


def validate_orro_setup_depone_pin(
    *, home: Path, depone_ref: str | None = None
) -> dict[str, Any]:
    provision = validate_depone_pin(home)
    depone = provision["depone"]
    depone_root = Path(str(depone["root"])).resolve(strict=False)
    expected_ref = (
        depone_ref or os.environ.get("WITNESSD_DEPONE_REF") or DEFAULT_DEPONE_REF
    )
    expected_commit = _git_commit_for_ref(depone_root, expected_ref)
    if str(depone["commit"]) != expected_commit:
        raise ProvisionError(ERR_ORRO_SETUP_DEPONE_PIN_MISMATCH)
    return provision


def run_depone_team_ledger(
    *,
    home: Path,
    ledger_path: Path,
    verdict_path: Path,
    trusted_observer_public_key_file: Path | None = None,
) -> dict[str, Any]:
    provision = validate_depone_pin(home)
    depone = provision["depone"]
    depone_root = Path(str(depone["root"])).resolve(strict=False)
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(depone_root)
        if not current_pythonpath
        else f"{depone_root}{os.pathsep}{current_pythonpath}"
    )
    if trusted_observer_public_key_file is not None:
        env["DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"] = str(
            trusted_observer_public_key_file
        )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "depone",
            "team-ledger",
            "--ledger",
            str(ledger_path),
            "--base-dir",
            str(ledger_path.parent),
            "--out",
            str(verdict_path),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0 or not verdict_path.is_file():
        raise ProvisionError(ERR_WITNESSD_DEPONE_VERIFY_FAILED)
    return json.loads(verdict_path.read_text(encoding="utf-8"))


def build_orro_engine_lock(*, home: Path, witnessd_root: Path) -> dict[str, Any]:
    try:
        provision = validate_depone_pin(home)
    except ProvisionError as exc:
        if exc.code == ERR_WITNESSD_DEPONE_PIN_MISMATCH:
            raise ProvisionError(ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISMATCH) from exc
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_DEPONE_PIN_MISSING) from exc

    depone = provision["depone"]
    depone_root = Path(str(depone["root"])).resolve(strict=False)
    witnessd_identity = _witnessd_identity(witnessd_root.resolve(strict=False))
    payload: dict[str, Any] = {
        "kind": ORRO_ENGINE_LOCK_KIND,
        "schema_version": ORRO_ENGINE_LOCK_SCHEMA_VERSION,
        "witnessd": {"repository": "Moonweave-Systems/witnessd", **witnessd_identity},
        "depone": {
            "repository": "Moonweave-Systems/Depone",
            "commit": str(depone["commit"]),
        },
        "boundary": {
            "approves_merge": False,
            "raises_assurance": False,
            "executes_commands": False,
            "verifies_evidence": False,
        },
    }
    witnessd_ref = _git_ref_name(witnessd_root.resolve(strict=False))
    depone_ref = _git_ref_name(depone_root)
    if witnessd_ref:
        payload["witnessd"]["ref_name"] = witnessd_ref
    if depone_ref:
        payload["depone"]["ref_name"] = depone_ref
    return payload


def check_orro_engine_lock(
    *, home: Path, witnessd_root: Path, lock_path: Path
) -> dict[str, Any]:
    expected = _load_orro_engine_lock(lock_path)
    current = build_orro_engine_lock(home=home, witnessd_root=witnessd_root)
    mismatches = []
    for field in _engine_lock_comparison_fields():
        expected_value = _get_nested(expected, field)
        current_value = _get_nested(current, field)
        if expected_value != current_value:
            mismatches.append(
                {
                    "field": field,
                    "expected": expected_value,
                    "current": current_value,
                }
            )
    locked = not mismatches
    payload: dict[str, Any] = {
        "command": "orro engine-lock check",
        "kind": "orro-engine-lock-check",
        "schema_version": "1.0",
        "lock": str(lock_path),
        "locked": locked,
        "mismatches": mismatches,
        "boundary": dict(current["boundary"]),
    }
    if not locked:
        payload["error"] = {
            "code": ERR_ORRO_ENGINE_LOCK_MISMATCH,
            "message": "ORRO engine lock does not match the current environment",
        }
    return payload


def _load_orro_engine_lock(lock_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_LOAD_FAILED) from exc
    if not isinstance(payload, dict):
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_INVALID)
    if payload.get("kind") != ORRO_ENGINE_LOCK_KIND:
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_INVALID)
    if payload.get("schema_version") != ORRO_ENGINE_LOCK_SCHEMA_VERSION:
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_INVALID)
    for section in ("witnessd", "depone", "boundary"):
        if not isinstance(payload.get(section), dict):
            raise ProvisionError(ERR_ORRO_ENGINE_LOCK_INVALID)
    return payload


def _engine_lock_comparison_fields() -> tuple[str, ...]:
    return (
        "kind",
        "schema_version",
        "witnessd.repository",
        "witnessd.commit",
        "witnessd.version",
        "witnessd.source",
        "depone.repository",
        "depone.commit",
        "boundary.approves_merge",
        "boundary.raises_assurance",
        "boundary.executes_commands",
        "boundary.verifies_evidence",
    )


def _get_nested(payload: dict[str, Any], field: str) -> Any:
    value: Any = payload
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _resolve_depone_root(config: InitConfig) -> tuple[Path, str, bool]:
    if config.depone_root is not None:
        root = config.depone_root.resolve(strict=False)
        source = "local-checkout"
    else:
        env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
        if env_root:
            root = Path(env_root).expanduser().resolve(strict=False)
            source = "env-checkout"
        else:
            sibling = config.witnessd_root.resolve(strict=False).parent / "depone"
            if (sibling / "depone").is_dir():
                root = sibling.resolve(strict=False)
                source = "sibling-checkout"
            elif not config.network_allowed:
                raise ProvisionError(ERR_WITNESSD_INIT_NETWORK_REQUIRED)
            else:
                root = _provision_depone_checkout(config)
                source = "setup-clone"
    if not (root / "depone").is_dir():
        raise ProvisionError(ERR_WITNESSD_DEPONE_ROOT_INVALID)
    _git_commit(root)
    return root, source, source == "setup-clone"


def _provision_depone_checkout(config: InitConfig) -> Path:
    target = config.home.resolve(strict=False) / "depone-pinned"
    if (target / "depone").is_dir():
        _git_commit(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    repository = (
        config.depone_repository
        or os.environ.get("WITNESSD_DEPONE_REPOSITORY")
        or DEFAULT_DEPONE_REPOSITORY
    )
    ref = (
        config.depone_ref or os.environ.get("WITNESSD_DEPONE_REF") or DEFAULT_DEPONE_REF
    )
    if ref and re.fullmatch(r"[0-9a-f]{40}", ref):
        commands = (
            [
                "git",
                "clone",
                "--no-checkout",
                "--filter=blob:none",
                repository,
                str(target),
            ],
            ["git", "-C", str(target), "fetch", "--depth=1", "origin", ref],
            ["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"],
        )
    else:
        command = ["git", "clone", "--depth=1"]
        if ref:
            command.extend(["--branch", ref])
        command.extend([repository, str(target)])
        commands = (command,)
    for command in commands:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ProvisionError(ERR_WITNESSD_DEPONE_PROVISION_FAILED)
    if not (target / "depone").is_dir():
        raise ProvisionError(ERR_WITNESSD_DEPONE_ROOT_INVALID)
    return target


def _build_provision(
    *,
    witnessd_root: Path,
    depone_root: Path,
    network_used: bool,
    depone_source: str,
    team_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provision = {
        "kind": PROVISION_KIND,
        "schema_version": PROVISION_SCHEMA_VERSION,
        "witnessd": {
            "root": str(witnessd_root),
            "commit": _git_commit_optional(witnessd_root),
        },
        "depone": {
            "root": str(depone_root),
            "commit": _git_commit(depone_root),
            "network_used": network_used,
            "source": depone_source,
        },
        "boundary": {
            "setup_may_use_network": True,
            "runtime_may_use_network": False,
            "verify_may_use_network": False,
        },
    }
    if team_ref is not None:
        provision["team_ref"] = team_ref
    return provision


def _build_team_ref(team_path: Path | None) -> dict[str, Any] | None:
    if team_path is None:
        return None
    from witnessd.role_capability import load_rolepack_file

    payload = load_rolepack_file(str(team_path))
    digest = hashlib.sha256(team_path.read_bytes()).hexdigest()
    return {
        "path": str(team_path),
        "sha256": digest,
        "kind": str(payload["kind"]),
        "schema_version": str(payload["schema_version"]),
        "name": str(payload["name"]),
    }


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProvisionError(ERR_WITNESSD_DEPONE_ROOT_INVALID)
    return completed.stdout.strip()


def _witnessd_identity(root: Path) -> dict[str, str | None]:
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_WITNESSD_IDENTITY) from exc
    output = probe.stdout.strip()
    if probe.returncode == 0 and output == "true":
        try:
            commit = _git_commit(root)
        except ProvisionError as exc:
            raise ProvisionError(ERR_ORRO_ENGINE_LOCK_WITNESSD_IDENTITY) from exc
        source = "git-checkout"
    elif probe.returncode == 0 and output not in {"", "false"}:
        raise ProvisionError(ERR_ORRO_ENGINE_LOCK_WITNESSD_IDENTITY)
    else:
        commit = None
        source = "pip-package"
    try:
        version = importlib.metadata.version("witnessd")
    except importlib.metadata.PackageNotFoundError:
        version = WITNESSD_PACKAGE_VERSION_FALLBACK
    return {"commit": commit, "version": version, "source": source}


def _git_commit_for_ref(root: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProvisionError(ERR_ORRO_SETUP_DEPONE_PIN_MISMATCH)
    return completed.stdout.strip()


def _git_commit_is_ancestor(root: Path, *, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _git_commit_optional(root: Path) -> str:
    """Like _git_commit but tolerant of a non-git root.

    An installed witnessd package (e.g. under site-packages from `pip install`)
    is not a git checkout, so `git rev-parse HEAD` there fails. The witnessd
    provenance commit is informational metadata, so degrade to "unknown" rather
    than aborting `orro init` — the depone root stays strict via _git_commit.
    """
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def _git_ref_name(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    ref_name = completed.stdout.strip()
    return ref_name or None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
