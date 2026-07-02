"""Per-spawn isolation probing and boundary verification."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

ISOLATION_MODEL = "uid-boundary-unwritable-observer-dir"
UID_OBSERVER_LAUNCHED_ISOLATION_MODEL = (
    "uid-boundary-observer-launched-unwritable-observer-dir"
)
CONTAINER_ISOLATION_MODEL = "container-boundary-unwritable-observer-dir"


def verify_isolation_boundary(facts: Any) -> dict[str, Any]:
    """Decide whether supplied facts establish a real privilege boundary."""

    if not isinstance(facts, dict):
        return {
            "model": ISOLATION_MODEL,
            "boundary": False,
            "runner_uid": None,
            "observer_uid": None,
            "observer_dir_writable_by_runner": None,
            "reasons": ["isolation facts must be an object"],
        }

    model = facts.get("model", ISOLATION_MODEL)
    if model == CONTAINER_ISOLATION_MODEL:
        return _verify_container_isolation_boundary(facts)
    if model == UID_OBSERVER_LAUNCHED_ISOLATION_MODEL:
        return _verify_uid_isolation_boundary(
            facts,
            model=UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
            observer_launch_required=True,
        )
    if model != ISOLATION_MODEL:
        return {
            "model": model if isinstance(model, str) else None,
            "boundary": False,
            "runner_uid": None,
            "observer_uid": None,
            "observer_dir_writable_by_runner": None,
            "reasons": ["unknown isolation model"],
        }

    return _verify_uid_isolation_boundary(
        facts, model=ISOLATION_MODEL, observer_launch_required=False
    )


def _verify_uid_isolation_boundary(
    facts: dict[str, Any], *, model: str, observer_launch_required: bool
) -> dict[str, Any]:
    runner_uid = facts.get("runner_uid")
    observer_uid = facts.get("observer_uid")
    writable = facts.get("observer_dir_writable_by_runner")
    observer_dir_mode = facts.get("observer_dir_mode")
    observer_launched = facts.get("observer_launched")
    if not isinstance(observer_launched, bool):
        observer_launched = None
    boundary = True
    reasons: list[str] = []

    if not isinstance(runner_uid, int) or not isinstance(observer_uid, int):
        boundary = False
        reasons.append("runner_uid and observer_uid must both be known integers")
    elif runner_uid == observer_uid:
        boundary = False
        reasons.append("runner and observer share the same uid (no privilege boundary)")
    elif runner_uid == 0:
        boundary = False
        reasons.append("root runner uid cannot establish a uid privilege boundary")

    if writable is not False:
        boundary = False
        reasons.append("observer dir must be proven not writable by the runner")

    if observer_launch_required and observer_launched is not True:
        boundary = False
        reasons.append("runner must be observer-launched")

    verified = {
        "model": model,
        "boundary": boundary,
        "runner_uid": runner_uid if isinstance(runner_uid, int) else None,
        "observer_uid": observer_uid if isinstance(observer_uid, int) else None,
        "observer_dir_writable_by_runner": writable
        if isinstance(writable, bool)
        else None,
        "reasons": reasons,
    }
    if isinstance(observer_dir_mode, str):
        verified["observer_dir_mode"] = observer_dir_mode
    if model == UID_OBSERVER_LAUNCHED_ISOLATION_MODEL:
        verified["observer_launched"] = observer_launched
    return verified


def _verify_container_isolation_boundary(facts: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    runner_uid = facts.get("runner_uid")
    observer_uid = facts.get("observer_uid")
    writable = facts.get("observer_dir_writable_by_runner")
    container = facts.get("container")
    container = container if isinstance(container, dict) else {}

    boundary = True
    if not isinstance(observer_uid, int):
        boundary = False
        reasons.append("observer_uid must be a known integer")
    if not isinstance(runner_uid, int):
        boundary = False
        reasons.append("runner_uid must be a known integer")
    if writable is not False:
        boundary = False
        reasons.append("observer dir must be proven not writable by the runner")
    if container.get("runtime") != "docker":
        boundary = False
        reasons.append("container runtime must be docker")
    if container.get("observer_launched") is not True:
        boundary = False
        reasons.append("container must be observer-launched")
    if not isinstance(container.get("container_id"), str) or not container.get(
        "container_id"
    ):
        boundary = False
        reasons.append("container_id must be known")
    if container.get("running") is not True:
        boundary = False
        reasons.append("container must be running when inspected")
    if container.get("observer_dir_mounted_rw") is not False:
        boundary = False
        reasons.append("observer dir must not be mounted writable in the container")
    mounts = container.get("mounts")
    if not isinstance(mounts, list):
        boundary = False
        reasons.append("container mounts must be recorded as a list")

    return {
        "model": CONTAINER_ISOLATION_MODEL,
        "boundary": boundary,
        "runner_uid": runner_uid if isinstance(runner_uid, int) else None,
        "observer_uid": observer_uid if isinstance(observer_uid, int) else None,
        "observer_dir_writable_by_runner": writable
        if isinstance(writable, bool)
        else None,
        "container": {
            "runtime": container.get("runtime")
            if container.get("runtime") == "docker"
            else None,
            "container_id": (
                container.get("container_id")
                if isinstance(container.get("container_id"), str)
                else None
            ),
            "image": container.get("image")
            if isinstance(container.get("image"), str)
            else None,
            "observer_launched": (
                container.get("observer_launched")
                if isinstance(container.get("observer_launched"), bool)
                else None
            ),
            "running": (
                container.get("running")
                if isinstance(container.get("running"), bool)
                else None
            ),
            "observer_dir_mounted_rw": (
                container.get("observer_dir_mounted_rw")
                if isinstance(container.get("observer_dir_mounted_rw"), bool)
                else None
            ),
            "mounts": mounts if isinstance(mounts, list) else None,
        },
        "reasons": reasons,
    }


def probe_lane_isolation(
    *,
    observer_dir: str,
    runner_uid: int | None,
    model: str = ISOLATION_MODEL,
    observer_launched: bool = False,
) -> dict[str, Any]:
    return probe_isolation_facts(
        Path(observer_dir),
        runner_uid=runner_uid,
        model=model,
        observer_launched=observer_launched,
    )


def probe_isolation_facts(
    observer_dir: Path,
    *,
    runner_uid: int | None,
    model: str = ISOLATION_MODEL,
    observer_launched: bool = False,
) -> dict[str, Any]:
    """Gather isolation facts on a real host."""

    facts: dict[str, Any] = {"runner_uid": runner_uid}
    if model != ISOLATION_MODEL:
        facts["model"] = model
    if model == UID_OBSERVER_LAUNCHED_ISOLATION_MODEL:
        facts["observer_launched"] = observer_launched
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        facts["observer_uid"] = None
        facts["observer_dir_writable_by_runner"] = None
        return facts

    observer_uid = getuid()
    facts["observer_uid"] = observer_uid
    try:
        st = os.stat(observer_dir)
    except OSError:
        facts["observer_dir_mode"] = None
        facts["observer_dir_writable_by_runner"] = None
        return facts

    facts["observer_dir_mode"] = f"{stat.S_IMODE(st.st_mode):04o}"
    foreign_owner = st.st_uid != observer_uid
    group_or_other_writable = bool(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    facts["observer_dir_writable_by_runner"] = bool(
        foreign_owner or group_or_other_writable
    )
    return facts


def probe_container_isolation_facts(
    observer_dir: Path, *, container_id: str, observer_launched: bool = False
) -> dict[str, Any]:
    """Gather Docker container isolation facts from the host observer."""

    facts: dict[str, Any] = {
        "model": CONTAINER_ISOLATION_MODEL,
        "runner_uid": None,
        "container": {
            "runtime": "docker",
            "container_id": container_id,
            "image": None,
            "observer_launched": observer_launched,
            "running": None,
            "observer_dir_mounted_rw": None,
            "mounts": None,
        },
    }
    getuid = getattr(os, "getuid", None)
    facts["observer_uid"] = getuid() if getuid is not None else None

    docker = shutil.which("docker")
    if docker is None:
        facts["observer_dir_writable_by_runner"] = None
        return facts
    result = subprocess.run(
        [docker, "inspect", container_id],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        facts["observer_dir_writable_by_runner"] = None
        return facts
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError:
        facts["observer_dir_writable_by_runner"] = None
        return facts
    if (
        not isinstance(inspected, list)
        or not inspected
        or not isinstance(inspected[0], dict)
    ):
        facts["observer_dir_writable_by_runner"] = None
        return facts

    record = inspected[0]
    container = _container_facts_from_docker_inspect(record, observer_dir)
    container["observer_launched"] = observer_launched
    facts["runner_uid"] = _runner_uid_from_docker_user(record)
    facts["container"] = container
    facts["observer_dir_writable_by_runner"] = container["observer_dir_mounted_rw"]
    return facts


def _container_facts_from_docker_inspect(
    record: dict[str, Any], observer_dir: Path
) -> dict[str, Any]:
    mounts = _mount_facts(record.get("Mounts"))
    mounted_rw = any(
        mount.get("rw") is True
        and _paths_overlap(Path(str(mount.get("source", ""))), observer_dir)
        for mount in mounts
    )
    state = record.get("State")
    config = record.get("Config")
    return {
        "runtime": "docker",
        "container_id": record.get("Id") if isinstance(record.get("Id"), str) else None,
        "image": (
            config.get("Image")
            if isinstance(config, dict) and isinstance(config.get("Image"), str)
            else record.get("Image")
            if isinstance(record.get("Image"), str)
            else None
        ),
        "observer_launched": False,
        "running": state.get("Running") if isinstance(state, dict) else None,
        "observer_dir_mounted_rw": mounted_rw,
        "mounts": mounts,
    }


def _mount_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    mounts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = item.get("Source")
        destination = item.get("Destination")
        mounts.append(
            {
                "source": source if isinstance(source, str) else "",
                "destination": destination if isinstance(destination, str) else "",
                "rw": bool(item.get("RW")),
            }
        )
    return mounts


def _paths_overlap(source: Path, observer_dir: Path) -> bool:
    if not str(source):
        return False
    source_path = source.expanduser().resolve(strict=False)
    observer_path = observer_dir.expanduser().resolve(strict=False)
    return _is_relative_to(observer_path, source_path) or _is_relative_to(
        source_path, observer_path
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _runner_uid_from_docker_user(record: dict[str, Any]) -> int | None:
    config = record.get("Config")
    user = config.get("User") if isinstance(config, dict) else None
    if user in (None, ""):
        return 0
    if not isinstance(user, str):
        return None
    uid_text = user.split(":", 1)[0]
    if uid_text.isdigit():
        return int(uid_text)
    return None


def isolation_self_test() -> None:
    _self_test()


def _self_test() -> None:
    isolated = verify_isolation_boundary(
        {
            "runner_uid": 1001,
            "observer_uid": 1002,
            "observer_dir_writable_by_runner": False,
        }
    )
    if isolated["boundary"] is not True:
        raise AssertionError(f"different uid + unwritable dir must hold: {isolated}")

    same_uid = verify_isolation_boundary(
        {
            "runner_uid": 1001,
            "observer_uid": 1001,
            "observer_dir_writable_by_runner": False,
        }
    )
    if same_uid["boundary"] is not False:
        raise AssertionError("same uid must not establish a boundary")

    writable = verify_isolation_boundary(
        {
            "runner_uid": 1001,
            "observer_uid": 1002,
            "observer_dir_writable_by_runner": True,
        }
    )
    if writable["boundary"] is not False:
        raise AssertionError("writable observer dir must not establish a boundary")

    missing = verify_isolation_boundary({"runner_uid": 1001})
    if missing["boundary"] is not False:
        raise AssertionError("missing facts must fail closed")

    root_runner = verify_isolation_boundary(
        {
            "runner_uid": 0,
            "observer_uid": 1002,
            "observer_dir_writable_by_runner": False,
        }
    )
    if root_runner["boundary"] is not False:
        raise AssertionError("root runner must not establish a uid boundary")

    missing_observer_launch = verify_isolation_boundary(
        {
            "model": UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
            "runner_uid": 1001,
            "observer_uid": 1002,
            "observer_dir_writable_by_runner": False,
        }
    )
    if missing_observer_launch["boundary"] is not False:
        raise AssertionError("observer-launched model must require launch receipt")


__all__ = [
    "CONTAINER_ISOLATION_MODEL",
    "ISOLATION_MODEL",
    "UID_OBSERVER_LAUNCHED_ISOLATION_MODEL",
    "isolation_self_test",
    "probe_container_isolation_facts",
    "probe_isolation_facts",
    "probe_lane_isolation",
    "verify_isolation_boundary",
]
