"""W5 atomic installer with fail-safe unreadable-config behavior."""

from __future__ import annotations

import json
import os
from typing import Any

ERR_WITNESSD_CONFIG_UNREADABLE = "ERR_WITNESSD_CONFIG_UNREADABLE"
ERR_WITNESSD_ORPHAN_SHIM = "ERR_WITNESSD_ORPHAN_SHIM"


class InstallerError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _read_config(config_path: str) -> dict[str, Any]:
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise InstallerError(ERR_WITNESSD_CONFIG_UNREADABLE) from exc
    if not isinstance(value, dict):
        raise InstallerError(ERR_WITNESSD_CONFIG_UNREADABLE)
    return value


def _atomic_write(dest_path: str, data: bytes) -> None:
    tmp = f"{dest_path}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest_path)
    dir_fd = os.open(os.path.dirname(dest_path) or ".", os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def list_orphan_shims(shim_dir: str, dest_dir: str) -> list[str]:
    if not os.path.isdir(shim_dir):
        return []
    installed = set(os.listdir(dest_dir)) if os.path.isdir(dest_dir) else set()
    orphans: list[str] = []
    for name in os.listdir(shim_dir):
        path = os.path.join(shim_dir, name)
        target = os.path.basename(os.path.realpath(path))
        if target not in installed:
            orphans.append(name)
    return sorted(orphans)


def atomic_install(
    *,
    payload_path: str,
    dest_dir: str,
    config_path: str,
    shim_dir: str,
    version: str,
) -> dict[str, Any]:
    _read_config(config_path)
    with open(payload_path, "rb") as handle:
        payload = handle.read()
    os.makedirs(dest_dir, exist_ok=True)
    os.makedirs(shim_dir, exist_ok=True)
    installed_path = os.path.join(dest_dir, f"{version}.txt")
    _atomic_write(installed_path, payload)
    shim_path = os.path.join(shim_dir, "witnessd")
    tmp_link = f"{shim_path}.tmp"
    if os.path.lexists(tmp_link):
        os.remove(tmp_link)
    os.symlink(installed_path, tmp_link)
    os.replace(tmp_link, shim_path)
    orphans = list_orphan_shims(shim_dir, dest_dir)
    if orphans:
        raise InstallerError(ERR_WITNESSD_ORPHAN_SHIM)
    return {"installed": True, "version": version, "path": installed_path}


def atomic_upgrade(
    *,
    payload_path: str,
    dest_dir: str,
    config_path: str,
    shim_dir: str,
    version: str,
) -> dict[str, Any]:
    return atomic_install(
        payload_path=payload_path,
        dest_dir=dest_dir,
        config_path=config_path,
        shim_dir=shim_dir,
        version=version,
    )


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        payload = os.path.join(tmp, "payload.txt")
        config = os.path.join(tmp, "config.json")
        dest = os.path.join(tmp, "dest")
        shim = os.path.join(tmp, "bin")
        with open(payload, "w", encoding="utf-8") as handle:
            handle.write("payload")
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"ok": True}, handle)
        result = atomic_install(
            payload_path=payload,
            dest_dir=dest,
            config_path=config,
            shim_dir=shim,
            version="v1",
        )
        assert result["installed"] is True
        assert list_orphan_shims(shim, dest) == []
