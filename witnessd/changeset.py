"""Change tracking for witnessd runner sandboxes."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import TypedDict


class ChangeRecord(TypedDict):
    path: str
    change_type: str
    old_path: str | None
    file_type: str
    mode: str | None
    content_sha256: str | None
    size: int | None


class SnapshotRecord(TypedDict):
    file_type: str
    mode: str | None
    content_sha256: str | None
    size: int | None


Baseline = dict[str, SnapshotRecord]


# witnessd's own runtime state dir (StateNamespace.state_dir). run_adapter_lane
# fails closed against this ever nesting inside an observed sandbox (see
# adapter_run.py's assert_separated(worktree, namespace.state_dir)); this is
# defense-in-depth for that same invariant at the snapshot layer, in case a
# caller's state_root resolves inside sandbox through some path the guard
# doesn't catch (e.g. a symlink swap between the guard check and this walk).
_EXCLUDED_DIR_NAMES = frozenset({".witnessd"})


def capture_snapshot(sandbox: str) -> Baseline:
    root = Path(sandbox)
    records: Baseline = {}
    for current_root, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if name not in _EXCLUDED_DIR_NAMES
            and not (Path(current_root) / name).is_symlink()
        ]
        for name in [*dirs, *files]:
            abs_path = Path(current_root) / name
            try:
                st = os.lstat(abs_path)
            except OSError:
                continue
            rel_path = abs_path.relative_to(root).as_posix()
            records[rel_path] = _snapshot_record(abs_path, st)
    return records


def capture_changeset(sandbox: str, baseline: Baseline) -> list[ChangeRecord]:
    return diff_snapshots(baseline, capture_snapshot(sandbox))


def diff_snapshots(before: Baseline, after: Baseline) -> list[ChangeRecord]:
    changes: list[ChangeRecord] = []
    for rel_path in sorted(set(before) | set(after)):
        old = before.get(rel_path)
        new = after.get(rel_path)
        if old == new:
            continue
        if old is None and new is not None:
            changes.append(_change(rel_path, "added", new))
        elif old is not None and new is None:
            changes.append(_change(rel_path, "deleted", old, deleted=True))
        elif old is not None and new is not None:
            changes.append(_change(rel_path, _classify_change(old, new), new))
    return changes


def touched_files(changes: list[ChangeRecord]) -> list[str]:
    return sorted(
        {record["path"] for record in changes if record.get("file_type") != "dir"}
    )


def _snapshot_record(path: Path, st: os.stat_result) -> SnapshotRecord:
    file_type = _file_type(st.st_mode)
    return {
        "file_type": file_type,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "content_sha256": _content_sha256(path, st) if file_type == "file" else None,
        "size": st.st_size if file_type == "file" else None,
    }


def _content_sha256(path: Path, st: os.stat_result) -> str | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            return None
        if (fd_stat.st_dev, fd_stat.st_ino) != (st.st_dev, st.st_ino):
            return None
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)


def _file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "submodule" if (mode & 0o170000) == 0o160000 else "file"


def _classify_change(old: SnapshotRecord, new: SnapshotRecord) -> str:
    if old["file_type"] != new["file_type"]:
        return "typechange"
    if old["mode"] != new["mode"]:
        return "mode"
    return "modified"


def _change(
    rel_path: str,
    change_type: str,
    record: SnapshotRecord,
    *,
    deleted: bool = False,
) -> ChangeRecord:
    return {
        "path": rel_path,
        "change_type": change_type,
        "old_path": None,
        "file_type": record["file_type"],
        "mode": record["mode"],
        "content_sha256": None if deleted else record["content_sha256"],
        "size": None if deleted else record["size"],
    }
