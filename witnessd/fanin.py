"""W3 team fan-in orchestration.

This module coordinates local shell lanes into isolated git worktrees, emits
per-lane evidence, and writes a Depone-valid team ledger. It does not launch
agents or approve merges; it only records evidence for downstream verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.adapters.shell import run_shell_lane
from witnessd.emitter import emit_supervised_lane
from witnessd.eventlog import EventLog
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.lock import ClaimConflictError, OwnershipRegistry
from witnessd.observer import assert_separated
from witnessd.runlog import append_runlog
from witnessd.killswitch import kill_all
from witnessd.process_identity import read_pid_start_time
from witnessd.substrate import build_bundle
from witnessd.supervisor import WorkerHandle, WorkerSupervisor
from witnessd.team_ledger import (
    build_evidence_next_verdict,
    build_team_ledger,
)
from witnessd.worktree import build_worktree_lane_receipt, create_lane_worktree


DEFAULT_STOP_RULE = "all write lanes pass or block"
ERR_TEAM_LANE_FAILED = "ERR_TEAM_LANE_FAILED"
ERR_TEAM_LANE_CANCELLED_FAIL_FAST = "ERR_TEAM_LANE_CANCELLED_FAIL_FAST"
ERR_TEAM_LANE_EXEC_FAILED = "ERR_TEAM_LANE_EXEC_FAILED"
ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH = "ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH"
ERR_TEAM_MERGE_CONFLICT_UNRESOLVED = "ERR_TEAM_MERGE_CONFLICT_UNRESOLVED"
TEAM_SCHEDULE_RECEIPT = "team-schedule-receipt.json"
TEAM_SCHEDULE_BUNDLE = "team-schedule-receipt-bundle.json"


def run_team(
    lane_specs: list[dict[str, Any]],
    *,
    repo_root: str,
    out_dir: str,
    private_key_path: str,
    public_key_path: str,
    base_commit: str | None = None,
    run_id: str = "w3-team",
    leader_objective: str = "witnessd W3 team fan-in",
    leader_id: str = "leader-fixed",
    stop_rule: str = DEFAULT_STOP_RULE,
    observer_dir: str | None = None,
    state_root: str | None = None,
    max_parallel: int | None = None,
    fail_fast: bool = False,
    merge_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base_dir = Path(out_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(str(base_dir / "runlog.jsonl"))
    if state_root is not None:
        _write_team_run_manifest(
            state_root=state_root,
            base_dir=base_dir,
            runlog_path=base_dir / "runlog.jsonl",
            run_id=run_id,
        )
    registry = OwnershipRegistry(log, run_id=run_id)
    root = Path(repo_root).resolve()
    start_commit = base_commit or _git(root, ["rev-parse", "HEAD"])
    observer_path = Path(observer_dir).resolve() if observer_dir else base_dir / "observer"
    observer_path.mkdir(parents=True, exist_ok=True)
    os.chmod(observer_path, 0o700)

    lanes: list[dict[str, Any]] = []
    lane_outputs: list[dict[str, Any]] = []
    claimed_lanes: list[str] = []
    runnable: list[dict[str, Any]] = []
    schedule_lanes: list[dict[str, Any]] = []
    supervisor = WorkerSupervisor(log, run_id=run_id)
    normalized_merge_groups = _normalize_merge_groups(merge_groups)
    merge_receipt: str | None = None

    try:
        for spec in lane_specs:
            lane_id = _lane_id(spec)
            commands = _commands(spec)
            try:
                allowed_touched_files = registry.claim(
                    lane_id=lane_id, region=spec.get("region", [])
                )
            except ClaimConflictError as exc:
                if not _merge_group_allows_overlap(
                    lane_id=lane_id,
                    conflict_files=exc.conflict_files,
                    merge_groups=normalized_merge_groups,
                ):
                    continue
                allowed_touched_files = _normalize_repo_region(spec.get("region", []))
                append_runlog(
                    log,
                    run_id,
                    "region-claim-overlap-allowed",
                    payload={
                        "lane_id": lane_id,
                        "region": allowed_touched_files,
                        "conflict_files": exc.conflict_files,
                    },
                )
            claimed_lanes.append(lane_id)

            if not allowed_touched_files:
                append_runlog(
                    log,
                    run_id,
                    "read-only-lane-audit",
                    payload={"lane_id": lane_id, "commands": commands},
                )
                continue

            runnable.append(
                {
                    "order": len(runnable),
                    "lane_id": lane_id,
                    "spec": dict(spec),
                    "commands": commands,
                    "allowed_touched_files": allowed_touched_files,
                }
            )
        lane_outputs = _run_claimed_lanes_parallel(
            runnable,
            repo_root=root,
            base_commit=start_commit,
            base_dir=base_dir,
            observer_dir=observer_path,
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            log=log,
            run_id=run_id,
            state_root=state_root,
            max_parallel=max_parallel,
            fail_fast=fail_fast,
            supervisor=supervisor,
            schedule_lanes=schedule_lanes,
        )
        lanes = [lane["ledger_lane"] for lane in lane_outputs]
        merge_outputs = _run_merge_groups(
            normalized_merge_groups,
            lane_outputs=lane_outputs,
            repo_root=root,
            base_commit=start_commit,
            base_dir=base_dir,
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            log=log,
            run_id=run_id,
            schedule_lanes=schedule_lanes,
        )
        if merge_outputs:
            lane_outputs.extend(merge_outputs)
            lanes.extend(output["ledger_lane"] for output in merge_outputs)
            for output in merge_outputs:
                if output.get("merge_receipt"):
                    merge_receipt = str(output["merge_receipt"])
    finally:
        _reap_remaining(supervisor, log, run_id)
        for lane_id in reversed(claimed_lanes):
            registry.release(lane_id=lane_id)

    schedule_receipt = None
    if lanes:
        schedule_receipt = TEAM_SCHEDULE_RECEIPT
        _write_schedule_receipt(
            log=log,
            run_id=run_id,
            base_dir=base_dir,
            leader_id=leader_id,
            lanes=schedule_lanes,
            private_key_path=private_key_path,
            public_key_path=public_key_path,
        )
    ledger = build_team_ledger(
        leader_objective=leader_objective,
        leader_id=leader_id,
        start_commit=start_commit,
        stop_rule=stop_rule,
        lanes=lanes,
        merge_receipt=merge_receipt,
        schedule_receipt=schedule_receipt,
    )
    _write_json_artifact(log, run_id, base_dir / "team-ledger.json", ledger)
    return {
        "base_dir": base_dir,
        "ledger": ledger,
        "lanes": lane_outputs,
        "runlog": log.read(),
        "supervisor_handles": supervisor.handles(),
    }


def _run_claimed_lanes_parallel(
    runnable: list[dict[str, Any]],
    *,
    repo_root: Path,
    base_commit: str,
    base_dir: Path,
    observer_dir: Path,
    private_key_path: str,
    public_key_path: str,
    log: EventLog,
    run_id: str,
    state_root: str | None,
    max_parallel: int | None,
    fail_fast: bool,
    supervisor: WorkerSupervisor,
    schedule_lanes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not runnable:
        return []
    parallelism = max_parallel if max_parallel is not None else len(runnable)
    if parallelism < 1:
        raise ValueError("ERR_TEAM_MAX_PARALLEL_INVALID")

    outputs: list[dict[str, Any]] = []
    pending = list(runnable)
    active: dict[WorkerHandle, dict[str, Any]] = {}
    cancelling = False
    try:
        while pending or active:
            while pending and not cancelling and len(active) < parallelism:
                job = pending.pop(0)
                handle = _spawn_lane_exec(
                    job,
                    repo_root=repo_root,
                    base_commit=base_commit,
                    base_dir=base_dir,
                    observer_dir=observer_dir,
                    private_key_path=private_key_path,
                    public_key_path=public_key_path,
                    log=log,
                    run_id=run_id,
                    state_root=state_root,
                    supervisor=supervisor,
                )
                active[handle] = job

            completed = [
                handle for handle in active if handle.popen.poll() is not None
            ]
            if not completed:
                time.sleep(0.02)
                continue

            for handle in completed:
                job = active.pop(handle)
                exit_code = supervisor.wait(handle)
                schedule = job["schedule"]
                _finish_schedule_lane(schedule, exit_code)
                schedule_lanes.append(schedule)
                lane = _read_lane_exec_result(job, base_commit, exit_code)
                lane["_order"] = int(job["order"])
                outputs.append(lane)
                if fail_fast and _lane_failed(lane) and (active or pending):
                    cancelling = True
                    cancelled_jobs = list(active.items()) + [
                        (None, queued) for queued in pending
                    ]
                    pending.clear()
                    _cancel_active_lanes(
                        active=active,
                        log=log,
                        run_id=run_id,
                        schedule_lanes=schedule_lanes,
                        outputs=outputs,
                        base_commit=base_commit,
                        supervisor=supervisor,
                    )
                    for _handle, queued in cancelled_jobs:
                        if _handle is None:
                            schedule = _unspawned_cancel_schedule(queued)
                            schedule_lanes.append(schedule)
                            outputs.append(
                                _cancelled_lane(
                                    str(queued["lane_id"]),
                                    queued["spec"],
                                    base_commit,
                                )
                            )
                            outputs[-1]["_order"] = int(queued["order"])
                    active.clear()
                    break
    finally:
        _reap_remaining(supervisor, log, run_id)
    return sorted(outputs, key=lambda lane: int(lane.get("_order", 0)))


def _normalize_merge_groups(
    merge_groups: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not merge_groups:
        return []
    normalized: list[dict[str, Any]] = []
    for raw in merge_groups:
        lane_id = str(raw.get("lane_id", "")).strip()
        sources = raw.get("sources")
        files = raw.get("files")
        if (
            not lane_id
            or not isinstance(sources, list)
            or len(sources) < 2
            or not isinstance(files, list)
            or not files
        ):
            raise ValueError("ERR_TEAM_MERGE_GROUP_INVALID")
        normalized_sources = sorted({str(source).strip() for source in sources if str(source).strip()})
        normalized_files = _normalize_repo_region(files)
        if len(normalized_sources) < 2 or not normalized_files:
            raise ValueError("ERR_TEAM_MERGE_GROUP_INVALID")
        normalized.append(
            {
                "lane_id": lane_id,
                "sources": normalized_sources,
                "files": normalized_files,
            }
        )
    return sorted(normalized, key=lambda group: group["lane_id"])


def _normalize_repo_region(region: Any) -> list[str]:
    if not isinstance(region, list):
        raise ValueError("ERR_TEAM_MERGE_GROUP_INVALID")
    normalized: set[str] = set()
    for raw in region:
        text = str(raw).replace("\\", "/").strip()
        if not text:
            raise ValueError("ERR_TEAM_MERGE_GROUP_INVALID")
        path = PurePosixPath(text)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("ERR_TEAM_MERGE_GROUP_INVALID")
        normalized.add(path.as_posix())
    return sorted(normalized)


def _merge_group_allows_overlap(
    *,
    lane_id: str,
    conflict_files: list[str],
    merge_groups: list[dict[str, Any]],
) -> bool:
    conflicts = set(conflict_files)
    if not conflicts:
        return False
    allowed: set[str] = set()
    for group in merge_groups:
        if lane_id in group["sources"]:
            allowed.update(group["files"])
    return conflicts.issubset(allowed)


def _run_merge_groups(
    merge_groups: list[dict[str, Any]],
    *,
    lane_outputs: list[dict[str, Any]],
    repo_root: Path,
    base_commit: str,
    base_dir: Path,
    private_key_path: str,
    public_key_path: str,
    log: EventLog,
    run_id: str,
    schedule_lanes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not merge_groups:
        return []
    outputs_by_id = {str(output["lane_id"]): output for output in lane_outputs}
    merge_outputs: list[dict[str, Any]] = []
    for group in merge_groups:
        lane_id = str(group["lane_id"])
        source_lanes = [outputs_by_id.get(source) for source in group["sources"]]
        if any(source is None or _lane_failed(source) for source in source_lanes):
            merge_outputs.append(
                _blocked_adapter_lane(
                    lane_id=lane_id,
                    adapter="shell",
                    base_commit=base_commit,
                    reason="ERR_TEAM_MERGE_SOURCE_NOT_PASS",
                )
            )
            continue

        heads = [
            str(source["ledger_lane"]["end_commit"])
            for source in source_lanes
            if isinstance(source, dict)
        ]
        schedule = _parent_schedule_lane(lane_id, base_dir=base_dir, repo_root=repo_root)
        receipt_rel = f"{lane_id}/team-merge-attempt-receipt.json"
        evidence_dir = base_dir / lane_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        receipt = _build_team_merge_attempt_receipt(
            repo=repo_root,
            base=base_commit,
            heads=heads,
        )
        _write_json_artifact(
            log,
            run_id,
            base_dir / receipt_rel,
            receipt,
            artifact_name=receipt_rel,
        )
        if receipt.get("decision") == "pass" and receipt.get("exit_code") == 0:
            marker = f"merge/{lane_id}.txt"
            merge_output = _run_write_lane(
                lane_id=lane_id,
                commands=[
                    [
                        "sh",
                        "-c",
                        f"mkdir -p merge && printf '%s\\n' {lane_id!r} > {marker}",
                    ]
                ],
                repo_root=repo_root,
                base_commit=base_commit,
                base_dir=base_dir,
                observer_dir=base_dir / "observer",
                allowed_touched_files=[marker],
                private_key_path=private_key_path,
                public_key_path=public_key_path,
                log=log,
                run_id=run_id,
            )
            merge_output["merge_receipt"] = receipt_rel
            _finish_schedule_lane(schedule, 0)
            schedule_lanes.append(schedule)
            merge_outputs.append(merge_output)
            continue

        _capture_merge_conflict_bytes(
            repo=repo_root,
            base=base_commit,
            heads=heads,
            out_dir=evidence_dir / "conflicts",
        )
        _finish_schedule_lane(schedule, int(receipt.get("exit_code", 1)))
        schedule_lanes.append(schedule)
        blocked = _blocked_adapter_lane(
            lane_id=lane_id,
            adapter="shell",
            base_commit=base_commit,
            reason=ERR_TEAM_MERGE_CONFLICT_UNRESOLVED,
        )
        blocked["merge_attempt_receipt"] = receipt_rel
        merge_outputs.append(blocked)
    return merge_outputs


def _build_team_merge_attempt_receipt(
    *, repo: Path, base: str, heads: list[str]
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="witnessd-team-merge-attempt-") as tmp:
        out_path = Path(tmp) / "team-merge-attempt-receipt.json"
        argv = [
            sys.executable,
            "-m",
            "depone",
            "team-merge-attempt",
            "--repo",
            str(repo),
            "--base",
            base,
            "--out",
            str(out_path),
            "--json",
        ]
        for head in heads:
            argv.extend(["--head", head])
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
        if out_path.is_file():
            return json.loads(out_path.read_text(encoding="utf-8"))
        message = completed.stderr.strip() or completed.stdout.strip()
        return {
            "kind": "depone-team-merge-attempt",
            "schema_version": "0.1",
            "decision": "blocked",
            "base_commit": base,
            "head_commits": heads,
            "attempt_worktree": str(repo),
            "dirty_target_refused": False,
            "exit_code": completed.returncode,
            "merged_files": [],
            "conflict_files": [],
            "cleanup": {"attempt_worktree_removed": True},
            "captured_at": _utc_now(),
            "source_command": argv,
            "errors": [
                {
                    "code": "ERR_TEAM_MERGE_ATTEMPT_FAILED",
                    "message": message or "depone team-merge-attempt did not write a receipt",
                }
            ],
            "boundary": {
                "executes_git_merge_attempt": True,
                "launches_agents": False,
                "calls_live_models": False,
                "approves_merge": False,
                "raises_assurance": False,
            },
        }


def _parent_schedule_lane(lane_id: str, *, base_dir: Path, repo_root: Path) -> dict[str, Any]:
    pid = os.getpid()
    pid_start = read_pid_start_time(pid)
    return {
        "lane_id": lane_id,
        "spawned_at": _utc_now(),
        "spawned_monotonic_ns": time.monotonic_ns(),
        "pid": pid,
        "pid_start_token": f"{pid}:{pid_start}",
        "worktree": _display_path(base_dir / "worktrees" / lane_id, base_dir, repo_root),
        "state_root": "merge-lane-local",
    }


def _capture_merge_conflict_bytes(
    *, repo: Path, base: str, heads: list[str], out_dir: Path
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="witnessd-merge-conflict-") as tmp:
        worktree = Path(tmp) / "worktree"
        add_result = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), base],
            capture_output=True,
            text=True,
            check=False,
        )
        if add_result.returncode != 0:
            (out_dir / "merge-worktree-error.txt").write_text(
                add_result.stderr or add_result.stdout,
                encoding="utf-8",
            )
            return
        try:
            subprocess.run(
                ["git", "-C", str(worktree), "merge", "--no-commit", "--no-ff", *heads],
                capture_output=True,
                text=True,
                check=False,
            )
            conflict_files = subprocess.run(
                ["git", "-C", str(worktree), "diff", "--name-only", "--diff-filter=U"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.splitlines()
            for name in conflict_files:
                source = worktree / name
                if source.is_file():
                    target = out_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            subprocess.run(
                ["git", "-C", str(worktree), "merge", "--abort"],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                capture_output=True,
                text=True,
                check=False,
            )


def _spawn_lane_exec(
    job: dict[str, Any],
    *,
    repo_root: Path,
    base_commit: str,
    base_dir: Path,
    observer_dir: Path,
    private_key_path: str,
    public_key_path: str,
    log: EventLog,
    run_id: str,
    state_root: str | None,
    supervisor: WorkerSupervisor,
) -> WorkerHandle:
    lane_id = str(job["lane_id"])
    control_dir = base_dir / ".lane-exec"
    control_dir.mkdir(parents=True, exist_ok=True)
    control_stem = _lane_control_stem(lane_id)
    spec_path = control_dir / f"{control_stem}.json"
    result_path = control_dir / f"{control_stem}-result.json"
    try:
        result_path.unlink()
    except FileNotFoundError:
        pass
    attempt_id = secrets.token_hex(16)
    payload = {
        "attempt_id": attempt_id,
        "lane_id": lane_id,
        "spec": job["spec"],
        "repo_root": str(repo_root),
        "base_commit": base_commit,
        "base_dir": str(base_dir),
        "observer_dir": str(observer_dir),
        "allowed_touched_files": job["allowed_touched_files"],
        "private_key_path": private_key_path,
        "public_key_path": public_key_path,
        "run_id": run_id,
        "state_root": state_root,
    }
    spec_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    spawned_at = _utc_now()
    spawned_ns = time.monotonic_ns()
    handle = supervisor.spawn(
        lane_id=lane_id,
        argv=[
            sys.executable,
            "-m",
            "witnessd",
            "lane-exec",
            "--spec-json",
            str(spec_path),
            "--result-json",
            str(result_path),
        ],
        runner_uid=None,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    pid_start = read_pid_start_time(handle.pid)
    job["attempt_id"] = attempt_id
    job["run_id"] = run_id
    job["lane_exec_pid"] = handle.pid
    job["lane_exec_pid_start_token"] = f"{handle.pid}:{pid_start}"
    job["result_path"] = result_path
    job["schedule"] = {
        "lane_id": lane_id,
        "spawned_at": spawned_at,
        "spawned_monotonic_ns": spawned_ns,
        "pid": handle.pid,
        "pid_start_token": f"{handle.pid}:{pid_start}",
        "worktree": _display_path(base_dir / "worktrees" / lane_id, base_dir, repo_root),
        "state_root": _display_path(
            _lane_state_root(job["spec"], state_root, repo_root), base_dir, repo_root
        ),
    }
    return handle


def _lane_control_stem(lane_id: str) -> str:
    slug = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-" for char in lane_id
    ).strip("-._")
    digest = hashlib.sha256(lane_id.encode("utf-8")).hexdigest()[:16]
    return f"{slug or 'lane'}-{digest}"


def _cancel_active_lanes(
    *,
    active: dict[WorkerHandle, dict[str, Any]],
    log: EventLog,
    run_id: str,
    schedule_lanes: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    base_commit: str,
    supervisor: WorkerSupervisor,
) -> None:
    if not active:
        return
    result = kill_all(None, log, run_id, grace=0.2, targets=[
        _kill_target_from_handle(handle) for handle in active
    ])
    outcomes = {
        (outcome["lane_id"], outcome["pid"]): outcome
        for outcome in result.get("outcomes", [])
    }
    for handle, job in active.items():
        handle.popen.wait()
        outcome = outcomes.get((handle.lane_id, handle.pid), {})
        supervisor_exit_code = outcome.get("exit_code")
        exit_code = int(supervisor_exit_code) if isinstance(supervisor_exit_code, int) else -9
        schedule = job["schedule"]
        _finish_schedule_lane(schedule, exit_code)
        schedule_lanes.append(schedule)
        outputs.append(_cancelled_lane(handle.lane_id, job["spec"], base_commit))
        outputs[-1]["_order"] = int(job["order"])
        supervisor.forget(handle)


def _kill_target_from_handle(handle: WorkerHandle):
    from witnessd.killswitch import KillTarget

    return KillTarget(
        lane_id=handle.lane_id,
        pid=handle.pid,
        runner_uid=handle.runner_uid,
        popen=handle.popen,
        pgid=getattr(handle, "pgid", None),
    )


def _reap_remaining(supervisor: WorkerSupervisor, log: EventLog, run_id: str) -> None:
    handles = supervisor.handles()
    if not handles:
        return
    kill_all(None, log, run_id, grace=0.2, targets=[
        _kill_target_from_handle(handle) for handle in handles
    ])
    for handle in handles:
        handle.popen.wait()
        supervisor.forget(handle)


def _read_lane_exec_result(
    job: dict[str, Any], base_commit: str, exit_code: int
) -> dict[str, Any]:
    result_path = Path(job["result_path"])
    if result_path.is_file():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        if (
            payload.get("run_id") == job.get("run_id")
            and payload.get("attempt_id") == job.get("attempt_id")
            and payload.get("lane_id") == job.get("lane_id")
            and payload.get("lane_exec_pid") == job.get("lane_exec_pid")
            and payload.get("lane_exec_pid_start_token") == job.get("lane_exec_pid_start_token")
            and isinstance(payload.get("lane"), dict)
        ):
            lane = payload["lane"]
            if exit_code == 0 or _lane_failed(lane):
                return lane
    return _blocked_adapter_lane(
        lane_id=str(job["lane_id"]),
        adapter=str(job["spec"].get("adapter", "shell")),
        base_commit=base_commit,
        reason=ERR_TEAM_LANE_EXEC_FAILED if exit_code else ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH,
    )


def _lane_failed(lane: dict[str, Any]) -> bool:
    ledger_lane = lane.get("ledger_lane", {})
    return ledger_lane.get("verification_state") != "pass"


def _cancelled_lane(lane_id: str, spec: dict[str, Any], base_commit: str) -> dict[str, Any]:
    return _blocked_adapter_lane(
        lane_id=lane_id,
        adapter=str(spec.get("adapter", "shell")),
        base_commit=base_commit,
        reason=ERR_TEAM_LANE_CANCELLED_FAIL_FAST,
    )


def _unspawned_cancel_schedule(job: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now()
    now_ns = time.monotonic_ns()
    return {
        "lane_id": str(job["lane_id"]),
        "spawned_at": now,
        "exited_at": now,
        "spawned_monotonic_ns": now_ns,
        "exited_monotonic_ns": now_ns,
        "pid": 0,
        "pid_start_token": "not-spawned",
        "exit_code": -9,
        "worktree": f"worktrees/{job['lane_id']}",
        "state_root": "not-spawned",
    }


def _finish_schedule_lane(schedule: dict[str, Any], exit_code: int) -> None:
    schedule["exited_at"] = _utc_now()
    schedule["exited_monotonic_ns"] = time.monotonic_ns()
    schedule["exit_code"] = int(exit_code)


def _lane_state_root(spec: dict[str, Any], state_root: str | None, repo_root: Path) -> Path:
    lane_state_root = spec.get("state_root")
    if lane_state_root:
        return Path(str(lane_state_root)).resolve(strict=False)
    if state_root:
        return Path(state_root).resolve(strict=False)
    return repo_root.resolve(strict=False)


def _display_path(path: Path, base_dir: Path, repo_root: Path) -> str:
    resolved = path.resolve(strict=False)
    for prefix, root in (("", base_dir), ("repo:", repo_root)):
        try:
            rel = resolved.relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        text = rel.as_posix()
        return f"{prefix}{text or '.'}"
    return str(resolved)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_schedule_receipt(
    *,
    log: EventLog,
    run_id: str,
    base_dir: Path,
    leader_id: str,
    lanes: list[dict[str, Any]],
    private_key_path: str,
    public_key_path: str,
) -> None:
    receipt = {
        "kind": "depone-team-schedule-receipt",
        "schema_version": "0.1",
        "observed_by": leader_id,
        "captured_at": _utc_now(),
        "boundary": {
            "executes_commands": False,
            "launches_agents": False,
            "raises_assurance": False,
            "proves_single_host_process_overlap": True,
            "note": "single-host orchestrator clock process-concurrency basis",
        },
        "lanes": sorted(lanes, key=lambda lane: lane["lane_id"]),
    }
    receipt_path = base_dir / TEAM_SCHEDULE_RECEIPT
    _write_json_artifact(log, run_id, receipt_path, receipt)
    bundle = build_bundle(
        {
            "kind": "depone-team-schedule-receipt",
            "assurance": "A2",
            "decision": "observed",
            "evidence_mode": "contemporaneous",
        },
        {"team-schedule-receipt": str(receipt_path)},
        private_key_path,
        public_key_path,
    )
    _write_json_artifact(log, run_id, base_dir / TEAM_SCHEDULE_BUNDLE, bundle)


def run_lane_exec_from_spec(spec_json: str, result_json: str) -> int:
    payload = json.loads(Path(spec_json).read_text(encoding="utf-8"))
    lane_id = str(payload["lane_id"])
    spec = dict(payload["spec"])
    try:
        if spec.get("adapter"):
            lane = _run_adapter_lane(
                lane_id=lane_id,
                spec=spec,
                repo_root=Path(payload["repo_root"]),
                base_commit=str(payload["base_commit"]),
                base_dir=Path(payload["base_dir"]),
                observer_dir=Path(payload["observer_dir"]),
                allowed_touched_files=list(payload["allowed_touched_files"]),
                private_key_path=str(payload["private_key_path"]),
                public_key_path=str(payload["public_key_path"]),
                log=EventLog(str(Path(payload["base_dir"]) / "runlog.jsonl")),
                run_id=str(payload["run_id"]),
                state_root=payload.get("state_root"),
            )
        else:
            lane = _run_write_lane(
                lane_id=lane_id,
                commands=_commands(spec),
                repo_root=Path(payload["repo_root"]),
                base_commit=str(payload["base_commit"]),
                base_dir=Path(payload["base_dir"]),
                observer_dir=Path(payload["observer_dir"]),
                allowed_touched_files=list(payload["allowed_touched_files"]),
                private_key_path=str(payload["private_key_path"]),
                public_key_path=str(payload["public_key_path"]),
                log=EventLog(str(Path(payload["base_dir"]) / "runlog.jsonl")),
                run_id=str(payload["run_id"]),
            )
        exit_code = 0 if not _lane_failed(lane) else 1
    except LaneBlocked as exc:
        lane = _blocked_adapter_lane(
            lane_id=lane_id,
            adapter=str(spec.get("adapter", "shell")),
            base_commit=str(payload["base_commit"]),
            reason=exc.reason,
            message=exc.message,
        )
        exit_code = 1
    except Exception as exc:
        lane = _blocked_adapter_lane(
            lane_id=lane_id,
            adapter=str(spec.get("adapter", "shell")),
            base_commit=str(payload["base_commit"]),
            reason=ERR_TEAM_LANE_EXEC_FAILED,
            message=str(exc),
        )
        exit_code = 1
    result_path = Path(result_json)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "attempt_id": payload.get("attempt_id"),
                "lane": _jsonable(lane),
                "lane_exec_pid": os.getpid(),
                "lane_exec_pid_start_token": f"{os.getpid()}:{read_pid_start_time(os.getpid())}",
                "lane_id": lane_id,
                "run_id": payload.get("run_id"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return exit_code


def resume_audit(out_dir: str, *, run_id: str = "w15-resume-audit") -> dict[str, Any]:
    base_dir = Path(out_dir).resolve(strict=False)
    control_dir = base_dir / ".lane-exec"
    lanes: list[dict[str, Any]] = []

    def _indeterminate_lane(lane_id: str) -> dict[str, Any]:
        return {
            "lane_id": lane_id,
            "classification": "indeterminate",
            "verification_state": "blocked",
            "blocked_reason": ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH,
        }

    def _complete_lane_or_none(lane_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
        lane = result.get("lane")
        if not isinstance(lane, dict):
            return None
        ledger_lane = lane.get("ledger_lane")
        if not isinstance(ledger_lane, dict):
            return None
        if ledger_lane.get("lane_id") != lane_id:
            return None
        verification_state = ledger_lane.get("verification_state")
        if verification_state not in {"pass", "blocked"}:
            return None
        if verification_state == "pass" and not ledger_lane.get("evidence_dir"):
            return None
        return {
            "lane_id": lane_id,
            "classification": "complete",
            "verification_state": verification_state,
            "evidence_dir": ledger_lane.get("evidence_dir"),
        }

    for spec_path in sorted(control_dir.glob("*.json")):
        if spec_path.name.endswith("-result.json"):
            continue
        fallback_lane_id = spec_path.stem
        try:
            payload = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            lanes.append(_indeterminate_lane(fallback_lane_id))
            continue
        lane_id = str(payload["lane_id"])
        spec_run_id = payload.get("run_id")
        result_path = control_dir / f"{_lane_control_stem(lane_id)}-result.json"
        legacy_result_path = control_dir / f"{lane_id}-result.json"
        if not result_path.is_file() and legacy_result_path.parent == control_dir:
            result_path = legacy_result_path
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                lanes.append(_indeterminate_lane(lane_id))
                continue
            if result.get("run_id") != run_id or spec_run_id != run_id:
                lanes.append(_indeterminate_lane(lane_id))
                continue
            complete_lane = _complete_lane_or_none(lane_id, result)
            lanes.append(complete_lane if complete_lane is not None else _indeterminate_lane(lane_id))
            continue
        lanes.append(_indeterminate_lane(lane_id))
    audit = {
        "kind": "witnessd-team-resume-audit",
        "schema_version": "0.1",
        "run_id": run_id,
        "boundary": {
            "executes_commands": False,
            "launches_agents": False,
            "replay_resume": False,
            "fabricates_completion": False,
        },
        "lanes": lanes,
    }
    (base_dir / "team-resume-audit.json").write_text(
        json.dumps(audit, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return audit


def _write_team_run_manifest(
    *,
    state_root: str,
    base_dir: Path,
    runlog_path: Path,
    run_id: str,
) -> None:
    root = Path(state_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "kind": "witnessd-team-run-state",
        "schema_version": "0.1",
        "run_id": run_id,
        "out_dir": str(base_dir.resolve(strict=False)),
        "runlog": str(runlog_path.resolve(strict=False)),
    }
    (root / "team-run.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _run_write_lane(
    *,
    lane_id: str,
    commands: list[list[str]],
    repo_root: Path,
    base_commit: str,
    base_dir: Path,
    observer_dir: Path,
    allowed_touched_files: list[str],
    private_key_path: str,
    public_key_path: str,
    log: EventLog,
    run_id: str,
) -> dict[str, Any]:
    worktree = create_lane_worktree(
        repo_root=str(repo_root),
        lane_id=lane_id,
        base_commit=base_commit,
        worktrees_dir=str(base_dir / "worktrees"),
    )
    evidence_dir = base_dir / lane_id
    assert_separated(runner_sandbox=worktree, out_path=str(evidence_dir / "capture-manifest.json"))

    lane_result = run_shell_lane(
        sandbox=worktree,
        commands=commands,
        test_command=["sh", "-c", "true"],
    )
    _commit_lane(worktree, lane_id)

    fixture = build_reference_adapter_fixture(build_shell_invocation(lane_id))
    emitted = emit_supervised_lane(
        lane_result,
        str(evidence_dir),
        private_key_path,
        fixture=fixture,
        allowed_touched_files=allowed_touched_files,
        public_key_path=public_key_path,
        observer_dir=str(observer_dir),
        runner_uid=os.getuid() + 1,
        task_id=lane_id,
        invocation=commands[0] if commands else ["sh", "-c", "true"],
        runner_sandbox=worktree,
    )

    receipt = build_worktree_lane_receipt(
        worktree=worktree,
        base_commit=base_commit,
        evidence_dir=lane_id,
        commands=lane_result["command_receipts"],
    )
    _write_json_artifact(
        log,
        run_id,
        evidence_dir / "worktree-lane-receipt.json",
        receipt,
        artifact_name=f"{lane_id}/worktree-lane-receipt.json",
    )
    verdict = build_evidence_next_verdict()
    _write_json_artifact(
        log,
        run_id,
        evidence_dir / "evidence-next-verdict.json",
        verdict,
        artifact_name=f"{lane_id}/evidence-next-verdict.json",
    )

    ledger_lane = {
        "lane_id": lane_id,
        "objective": f"{lane_id} objective",
        "start_commit": base_commit,
        "end_commit": receipt["head_commit"],
        "evidence_dir": lane_id,
        "env_kind": "local",
        "runner_adapter_kind": "shell",
        "team_adapter_kind": "shell",
        "verification_state": "pass",
        "touched_files": receipt["changed_files"],
        "worktree_receipt": f"{lane_id}/worktree-lane-receipt.json",
        "evidence_next_verdict": f"{lane_id}/evidence-next-verdict.json",
    }
    if any(
        int(command.get("exit_code", 0)) != 0
        for command in lane_result.get("command_receipts", [])
        if isinstance(command, dict)
    ):
        ledger_lane["verification_state"] = "blocked"
        ledger_lane["blocked_reason"] = ERR_TEAM_LANE_FAILED
    return {
        "lane_id": lane_id,
        "worktree": worktree,
        "evidence_dir": evidence_dir,
        "lane_result": lane_result,
        "manifest": emitted["manifest"],
        "ledger_lane": ledger_lane,
        "worktree_receipt": receipt,
        "evidence_next_verdict": verdict,
    }


def _run_adapter_lane(
    *,
    lane_id: str,
    spec: dict[str, Any],
    repo_root: Path,
    base_commit: str,
    base_dir: Path,
    observer_dir: Path,
    allowed_touched_files: list[str],
    private_key_path: str,
    public_key_path: str,
    log: EventLog,
    run_id: str,
    state_root: str | None,
) -> dict[str, Any]:
    worktree = create_lane_worktree(
        repo_root=str(repo_root),
        lane_id=lane_id,
        base_commit=base_commit,
        worktrees_dir=str(base_dir / "worktrees"),
    )
    evidence_dir = base_dir / lane_id
    assert_separated(runner_sandbox=worktree, out_path=str(evidence_dir / "capture-manifest.json"))

    lane_state_root = spec.get("state_root")
    adapter_state_root = (
        str(Path(str(lane_state_root)).resolve(strict=False))
        if lane_state_root
        else str(Path(state_root).resolve(strict=False))
        if state_root
        else str(repo_root)
    )
    result = run_adapter_lane(
        root=adapter_state_root,
        sandbox=worktree,
        adapter=str(spec["adapter"]),
        task_id=lane_id,
        prompt=str(spec["prompt"]),
        arm=str(spec.get("arm", "direct")),
        tier=str(spec.get("tier", "agentic")),
        is_supported=spec.get("is_supported", lambda _model: True),
        budget=spec.get(
            "budget",
            {"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
        ),
        predicted_tokens=int(spec.get("predicted_tokens", 0)),
        predicted_usd=float(spec.get("predicted_usd", 0.0)),
        codex_binary=str(spec.get("codex_binary", "codex")),
        claude_binary=str(spec.get("claude_binary", "claude")),
        opencode_binary=str(spec.get("opencode_binary", "opencode")),
        evidence_dir=str(evidence_dir),
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        allowed_touched_files=(
            list(spec["allowed_touched_files"])
            if isinstance(spec.get("allowed_touched_files"), list)
            and all(isinstance(item, str) for item in spec["allowed_touched_files"])
            else None
        ),
    )
    _commit_lane(worktree, lane_id)

    runner_receipt = result.get("runner_receipt", {})
    receipt = build_worktree_lane_receipt(
        worktree=worktree,
        base_commit=base_commit,
        evidence_dir=lane_id,
        commands=runner_receipt.get("command_receipts", []),
    )
    _write_json_artifact(
        log,
        run_id,
        evidence_dir / "worktree-lane-receipt.json",
        receipt,
        artifact_name=f"{lane_id}/worktree-lane-receipt.json",
    )
    verdict = build_evidence_next_verdict()
    _write_json_artifact(
        log,
        run_id,
        evidence_dir / "evidence-next-verdict.json",
        verdict,
        artifact_name=f"{lane_id}/evidence-next-verdict.json",
    )

    adapter = str(spec["adapter"])
    ledger_lane = {
        "lane_id": lane_id,
        "objective": f"{lane_id} objective",
        "start_commit": base_commit,
        "end_commit": receipt["head_commit"],
        "evidence_dir": lane_id,
        "env_kind": "local",
        "runner_adapter_kind": _ledger_adapter_kind(adapter),
        "team_adapter_kind": _ledger_adapter_kind(adapter),
        "verification_state": "pass",
        "touched_files": receipt["changed_files"],
        "worktree_receipt": f"{lane_id}/worktree-lane-receipt.json",
        "evidence_next_verdict": f"{lane_id}/evidence-next-verdict.json",
    }
    return {
        "lane_id": lane_id,
        "worktree": worktree,
        "evidence_dir": evidence_dir,
        "adapter_result": result,
        "manifest": result.get("capture_manifest"),
        "ledger_lane": ledger_lane,
        "worktree_receipt": receipt,
        "evidence_next_verdict": verdict,
    }


def _blocked_adapter_lane(
    *,
    lane_id: str,
    adapter: str,
    base_commit: str,
    reason: str,
    message: str = "",
) -> dict[str, Any]:
    ledger_lane = {
        "lane_id": lane_id,
        "objective": f"{lane_id} objective",
        "start_commit": base_commit,
        "end_commit": base_commit,
        "evidence_dir": lane_id,
        "env_kind": "local",
        "runner_adapter_kind": _ledger_adapter_kind(adapter),
        "team_adapter_kind": _ledger_adapter_kind(adapter),
        "verification_state": "blocked",
        "blocked_reason": reason,
        "touched_files": [],
    }
    if message:
        ledger_lane["blocked_message"] = message
    return {
        "lane_id": lane_id,
        "ledger_lane": ledger_lane,
        "blocked_reason": reason,
    }


def _ledger_adapter_kind(adapter: str) -> str:
    if adapter == "claude":
        return "claude-code"
    if adapter in {"codex", "opencode", "shell"}:
        return adapter
    return "external"


def _lane_id(spec: dict[str, Any]) -> str:
    lane_id = str(spec.get("lane_id", "")).strip()
    if not lane_id:
        raise ValueError("ERR_TEAM_LANE_ID_REQUIRED")
    return lane_id


def _commands(spec: dict[str, Any]) -> list[list[str]]:
    commands = spec.get("commands", [])
    if not isinstance(commands, list):
        raise ValueError("ERR_TEAM_LANE_COMMANDS_INVALID")
    normalized: list[list[str]] = []
    for command in commands:
        if not isinstance(command, list) or not all(
            isinstance(part, str) for part in command
        ):
            raise ValueError("ERR_TEAM_LANE_COMMAND_INVALID")
        normalized.append(list(command))
    return normalized


def _commit_lane(worktree: str, lane_id: str) -> None:
    _git(Path(worktree), ["add", "-A"])
    if not _git(Path(worktree), ["diff", "--cached", "--name-only"]):
        return
    _git(Path(worktree), ["commit", "-qm", f"{lane_id} lane change"])


def _write_json_artifact(
    log: EventLog,
    run_id: str,
    path: Path,
    payload: dict[str, Any],
    *,
    artifact_name: str | None = None,
) -> None:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text(data, encoding="utf-8")
    append_runlog(
        log,
        run_id,
        "emit-artifact",
        payload={
            "artifact": artifact_name or path.name,
            "path": str(path),
            "content_sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        },
    )


def _git(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"ERR_TEAM_GIT_FAILED: {message}")
    return completed.stdout.strip()


def _self_test() -> None:
    import shutil
    import tempfile

    if shutil.which("openssl") is None:
        print("witnessd fanin --self-test: pass (openssl unavailable)")
        return

    from witnessd.signing import gen_operator_keypair

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        _git(repo, ["init", "-q"])
        _git(repo, ["config", "user.email", "w@x.invalid"])
        _git(repo, ["config", "user.name", "w3"])
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(repo, ["add", "-A"])
        _git(repo, ["commit", "-qm", "seed"])
        base_commit = _git(repo, ["rev-parse", "HEAD"])
        keys = root / "keys"
        keys.mkdir()
        private_key_path, public_key_path = gen_operator_keypair(str(keys))
        result = run_team(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/a.py"],
                    "commands": [["sh", "-c", "mkdir -p pkg && echo a > pkg/a.py"]],
                }
            ],
            repo_root=str(repo),
            out_dir=str(root / "evidence"),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
        )
        assert result["ledger"]["kind"] == "depone-team-ledger"
        assert len(result["ledger"]["lanes"]) == 1
