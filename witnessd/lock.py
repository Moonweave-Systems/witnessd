"""Ownership-region lock with runlog-audited claim/release/conflict events."""

from __future__ import annotations

import posixpath
from collections.abc import Sequence

from witnessd.runlog import append_runlog


class ClaimConflictError(RuntimeError):
    def __init__(self, conflict_files: list[str]) -> None:
        super().__init__(f"ERR_REGION_CLAIM_CONFLICT: {conflict_files}")
        self.conflict_files = conflict_files


def _normalize_region(region: Sequence[str]) -> list[str]:
    normalized: set[str] = set()
    for raw_path in region:
        path = str(raw_path).replace("\\", "/")
        path = posixpath.normpath(path)
        if path in ("", ".") or path.startswith("../") or path == "..":
            continue
        normalized.add(path)
    return sorted(normalized)


class OwnershipRegistry:
    def __init__(self, event_log, run_id: str = "team") -> None:
        self._log = event_log
        self._run_id = run_id
        self._owners: dict[str, str] = {}

    def claim(self, *, lane_id: str, region: Sequence[str]) -> list[str]:
        normalized = _normalize_region(region)
        conflict_files = [
            path
            for path in normalized
            if self._owners.get(path) is not None and self._owners[path] != lane_id
        ]
        if conflict_files:
            append_runlog(
                self._log,
                self._run_id,
                "claim-conflict",
                error_code="ERR_REGION_CLAIM_CONFLICT",
                payload={
                    "lane_id": lane_id,
                    "region": normalized,
                    "conflict_files": conflict_files,
                },
            )
            raise ClaimConflictError(conflict_files)

        for path in normalized:
            self._owners[path] = lane_id
        append_runlog(
            self._log,
            self._run_id,
            "region-claim",
            payload={"lane_id": lane_id, "region": normalized},
        )
        return normalized

    def release(self, *, lane_id: str) -> None:
        released = sorted(
            path for path, owner in list(self._owners.items()) if owner == lane_id
        )
        for path in released:
            del self._owners[path]
        append_runlog(
            self._log,
            self._run_id,
            "region-release",
            payload={"lane_id": lane_id, "region": released},
        )

    def owner_of(self, path: str) -> str | None:
        normalized = _normalize_region([path])
        if not normalized:
            return None
        return self._owners.get(normalized[0])


def _self_test() -> None:
    import os
    import tempfile

    from witnessd.eventlog import EventLog

    with tempfile.TemporaryDirectory() as tmp:
        log = EventLog(os.path.join(tmp, "runlog.jsonl"))
        registry = OwnershipRegistry(log)
        assert registry.claim(lane_id="L1", region=["b.py", "a.py"]) == [
            "a.py",
            "b.py",
        ]
        registry.release(lane_id="L1")
        registry.claim(lane_id="L2", region=["a.py"])
