from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from witnessd.cli._output import _emit_orro_error, _hash_file
from witnessd.orro_next import decide_next
from witnessd.orro_report import OrroReportError, build_report, render_text_report, write_report
from witnessd.orro_roadmap import (
    OrroRoadmapError,
    read_roadmap,
    read_roadmap_binding,
)
from witnessd.orro_task import discover_task_workspaces, task_worktree_path


STATUS_BOUNDARY = (
    "status is observed state + declared bindings; it is not proof, not approval, "
    "not assurance; marked-done (unverified) items are operator claims."
)
TIDY_BOUNDARY = (
    "tidy manages Git worktrees; it removes only aged check runs when --keep-checks "
    "is given, never flow/team evidence, and does not verify evidence, approve work, "
    "or raise assurance."
)


def resolve_home(args_home: str | None, repo: Path) -> Path:
    return Path(args_home or os.environ.get("WITNESSD_HOME") or (repo / ".witnessd")).resolve(strict=False)


def _cmd_orro_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve(strict=False)
    home = resolve_home(args.home, repo)
    if args.run_dir or args.latest:
        if args.run_dir and args.latest:
            _emit_orro_error(
                args,
                code="ERR_ORRO_STATUS_RUN_DIR_CONFLICT",
                message="orro status --latest cannot be combined with a run directory",
            )
            return 2
        run_dir = (
            Path(args.run_dir).resolve(strict=False)
            if args.run_dir
            else latest_run_dir(home)
        )
        if run_dir is None:
            _emit_orro_error(
                args,
                code="ERR_ORRO_STATUS_LATEST_NO_RUNS",
                message=f"no ORRO runs found under {home / 'runs'}",
            )
            return 2
        workstyle = (
            Path(args.workstyle_decision).resolve(strict=False)
            if args.workstyle_decision
            else None
        )
        try:
            code, payload = build_report(
                run_dir,
                home=home,
                workstyle_decision=workstyle,
                declared_intent=None,
                declared_intent_source=None,
            )
            if args.out:
                write_report(Path(args.out).resolve(strict=False), payload)
        except OrroReportError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
        if getattr(args, "_deprecated_alias", None) == "report":
            print(
                "deprecated: use orro status <run-dir> (this alias will be removed in a future release)",
                file=sys.stderr,
            )
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(render_text_report(payload), end="")
        return code
    try:
        payload = build_status(repo=repo, home=home)
    except OrroRoadmapError as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 2
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(render_status_text(payload))
    return 0


def _cmd_orro_tidy(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve(strict=False)
    home = resolve_home(args.home, repo)
    payload = build_tidy_inventory(repo=repo, home=home)
    if args.apply:
        payload = apply_tidy(repo=repo, inventory=payload, keep_checks=args.keep_checks)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(render_tidy_text(payload))
    return 0


def build_status(*, repo: Path, home: Path) -> dict[str, Any]:
    repo = repo.resolve(strict=False)
    home = home.resolve(strict=False)
    roadmap = read_roadmap(repo)
    run_dirs = _run_dirs(home)
    runs = [_status_run(run_dir, home=home) for run_dir in run_dirs]
    by_item: dict[str, list[dict[str, Any]]] = {}
    off_plan: list[dict[str, Any]] = []
    for run in runs:
        item_id = run.get("item_id")
        if isinstance(item_id, str):
            by_item.setdefault(item_id, []).append(run)
        else:
            off_plan.append({"run_dir": run["run_dir"], "state": run["state"]})

    items = [
        _roadmap_item_status(item, by_item.get(str(item["id"]), []), repo=repo)
        for item in (roadmap or {"items": []})["items"]
    ]
    for item in items:
        workspace = _task_workspace_status(repo, str(item["id"]))
        if workspace is not None:
            item["workspace"] = workspace
    off_plan.sort(key=lambda item: _path_newness(Path(item["run_dir"])), reverse=True)
    worktrees = _run_worktree_paths(run_dirs)
    receipts = _worktree_receipts(run_dirs)
    dirty_count = sum(
        1 for path in worktrees if _status_worktree_dirty(path, receipts.get(str(path)))
    )
    return {
        "kind": "orro-status",
        "schema_version": "0.1",
        "repo": str(repo),
        "home": str(home),
        "items": items,
        "off_plan": off_plan,
        "workspace": {
            "run_count": len(run_dirs),
            "worktree_count": len(worktrees),
            "worktree_bytes": sum(_tree_size(path) for path in worktrees),
            "dirty_worktree_count": dirty_count,
        },
        "boundary": STATUS_BOUNDARY,
    }


def render_status_text(payload: dict[str, Any]) -> str:
    lines = ["ORRO status", "Roadmap:"]
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        lines.append("  (no roadmap items)")
    else:
        for item in items:
            line = f"- {item['id']}: {item['status']} — {item['title']}"
            if item.get("evidence_ref"):
                line += f" — evidence: {item['evidence_ref']}"
            elif item.get("latest_run"):
                line += f" — {item['run_state']}: {item['latest_run']}"
            lines.append(line)
            if item.get("workspace"):
                lines.append(f"  workspace: {item['workspace']}")
            for step in item.get("steps", []):
                step_line = f"  - step {step['id']}: {step['state']}"
                if step.get("evidence_ref"):
                    step_line += f" — evidence: {step['evidence_ref']}"
                elif step.get("run_dir"):
                    step_line += f" — run: {step['run_dir']}"
                lines.append(step_line)
            next_step = item.get("next_step")
            if isinstance(next_step, dict):
                lines.append(
                    f"  Next step {next_step['id']}: {next_step['suggested_next_command']}"
                )
    lines.append("Off-plan runs:")
    off_plan = payload.get("off_plan")
    if not isinstance(off_plan, list) or not off_plan:
        lines.append("  (none)")
    else:
        for run in off_plan:
            lines.append(f"- {run['run_dir']}: {run['state']}")
    workspace = payload["workspace"]
    lines.append(
        "Workspace: "
        f"runs={workspace['run_count']}, worktrees={workspace['worktree_count']}, "
        f"size={workspace['worktree_bytes']} bytes, "
        f"dirty={workspace['dirty_worktree_count']}"
    )
    lines.append(f"Boundary: {payload['boundary']}")
    return "\n".join(lines)


def build_tidy_inventory(*, repo: Path, home: Path) -> dict[str, Any]:
    repo = repo.resolve(strict=False)
    home = home.resolve(strict=False)
    run_dirs = _run_dirs(home)
    decisions = {
        str(run_dir): str(decide_next(run_dir, home=home)[1].get("decision", "blocked"))
        for run_dir in run_dirs
    }
    receipts = _worktree_receipts(run_dirs)
    registered = _registered_worktrees(repo)
    registered_by_path = {record["path"]: record for record in registered}
    worktrees: list[dict[str, Any]] = []
    seen: set[str] = set()

    for run_dir in run_dirs:
        worktrees_dir = run_dir / "worktrees"
        if not worktrees_dir.is_dir():
            continue
        for path in sorted(
            (item.resolve(strict=False) for item in worktrees_dir.iterdir() if item.is_dir()),
            key=str,
        ):
            path_text = str(path)
            seen.add(path_text)
            worktrees.append(
                _tidy_worktree_record(
                    path=path,
                    run_dir=run_dir,
                    run_state=decisions[str(run_dir)],
                    receipt=receipts.get(path_text),
                    registration=registered_by_path.get(path_text),
                )
            )

    outside: list[dict[str, Any]] = []
    task_root = (repo / ".orro" / "worktrees").resolve(strict=False)
    for registration in registered:
        path = Path(registration["path"])
        path_text = str(path)
        if path_text in seen:
            continue
        if path.parent == task_root:
            continue
        owner = _owning_run(path, home=home)
        if owner is not None and owner in run_dirs:
            worktrees.append(
                _tidy_worktree_record(
                    path=path,
                    run_dir=owner,
                    run_state=decisions[str(owner)],
                    receipt=receipts.get(path_text),
                    registration=registration,
                )
            )
            seen.add(path_text)
            continue
        outside.append(
            {
                "path": path_text,
                "exists": path.is_dir(),
                "registered": True,
                "branch": registration.get("branch"),
                "head_commit": registration.get("head_commit"),
                "prunable": bool(registration.get("prunable")) or not path.exists(),
            }
        )
    worktrees.sort(key=lambda item: item["path"])
    outside.sort(key=lambda item: item["path"])
    task_status = {
        str(item["id"]): str(item.get("status", "not-started"))
        for item in build_status(repo=repo, home=home).get("items", [])
    }
    task_worktrees = [
        _task_tidy_record(record, item_status=task_status.get(str(record["item_id"]), "unknown item"))
        for record in discover_task_workspaces(repo)
    ]
    check_runs: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        if not run_dir.name.startswith("check-") or not (run_dir / "companion-manifest.json").is_file():
            continue
        check_runs.append({"path": str(run_dir), "action": "kept", "reason": "dry-run"})
    return {
        "kind": "orro-tidy",
        "schema_version": "0.1",
        "mode": "dry-run",
        "repo": str(repo),
        "home": str(home),
        "worktrees": worktrees,
        "task_worktrees": task_worktrees,
        "registered_outside_runs": outside,
        "check_runs": check_runs,
        "boundary": TIDY_BOUNDARY,
    }


def apply_tidy(
    *, repo: Path, inventory: dict[str, Any], keep_checks: int | None = None
) -> dict[str, Any]:
    repo = repo.resolve(strict=False)
    actions: list[dict[str, str]] = []
    prune_needed = False
    for item in inventory.get("worktrees", []):
        path = Path(str(item["path"]))
        if not path.exists() and item.get("registered"):
            actions.append({"path": str(path), "action": "pruned", "reason": "registered path missing"})
            prune_needed = True
            continue
        live = _live_worktree_state(path)
        if live["dirty"] is True:
            actions.append({"path": str(path), "action": "kept", "reason": "dirty"})
            continue
        if live["dirty"] is None:
            actions.append(
                {
                    "path": str(path),
                    "action": "kept",
                    "reason": f"live dirty check failed: {live['error']}",
                }
            )
            continue
        run_state = str(item.get("run_state", "blocked"))
        if run_state != "complete":
            actions.append(
                {
                    "path": str(path),
                    "action": "kept",
                    "reason": f"run state {run_state}",
                }
            )
            continue
        completed = _git(repo, ["worktree", "remove", str(path)])
        if completed.returncode == 0:
            actions.append({"path": str(path), "action": "removed", "reason": "clean complete run"})
            prune_needed = True
        else:
            actions.append(
                {
                    "path": str(path),
                    "action": "kept",
                    "reason": "git worktree remove failed: "
                    + (completed.stderr.strip() or completed.stdout.strip() or "unknown error"),
                }
            )

    current_status = {
        str(item["id"]): str(item.get("status", "not-started"))
        for item in build_status(
            repo=repo, home=Path(str(inventory.get("home", repo / ".witnessd")))
        ).get("items", [])
    }
    for item in inventory.get("task_worktrees", []):
        path = Path(str(item["path"]))
        if not item.get("descriptor_valid"):
            actions.append({"path": str(path), "action": "kept", "reason": "unverified descriptor"})
            continue
        live = _live_worktree_state(path)
        if live["dirty"] is True:
            actions.append({"path": str(path), "action": "kept", "reason": "dirty"})
            continue
        if live["dirty"] is None:
            actions.append({"path": str(path), "action": "kept", "reason": f"live dirty check failed: {live['error']}"})
            continue
        item_status = current_status.get(str(item["item_id"]), "unknown item")
        if item_status != "done (verified)":
            actions.append({"path": str(path), "action": "kept", "reason": f"item status {item_status}"})
            continue
        metadata = []
        for name in (".orro-task.json", "task-open-receipt.json"):
            metadata_path = path / name
            if metadata_path.is_file():
                metadata.append((metadata_path, metadata_path.read_bytes()))
                metadata_path.unlink()
        completed = _git(repo, ["worktree", "remove", str(path)])
        if completed.returncode == 0:
            actions.append({"path": str(path), "action": "removed", "reason": "clean done (verified) item"})
            prune_needed = True
        else:
            for metadata_path, content in metadata:
                metadata_path.write_bytes(content)
            actions.append({"path": str(path), "action": "kept", "reason": "git worktree remove failed: " + (completed.stderr.strip() or completed.stdout.strip() or "unknown error")})

    for item in inventory.get("registered_outside_runs", []):
        path = Path(str(item["path"]))
        if item.get("registered") and not path.exists():
            actions.append({"path": str(path), "action": "pruned", "reason": "registered path missing"})
            prune_needed = True
        else:
            actions.append({"path": str(path), "action": "kept", "reason": "outside run directories"})
    if keep_checks is not None:
        if keep_checks < 0:
            raise ValueError("--keep-checks must be zero or greater")
        evidence_paths: set[Path] = set()
        status = build_status(
            repo=repo, home=Path(str(inventory.get("home", repo / ".witnessd")))
        )
        for item in status.get("items", []):
            if not isinstance(item, dict):
                continue
            for candidate in [item, *item.get("steps", [])]:
                if not isinstance(candidate, dict) or candidate.get("state", candidate.get("status")) not in {"done (verified)", "companion-pass", "complete", "ready-for-handoff"}:
                    continue
                evidence_ref = candidate.get("evidence_ref")
                if isinstance(evidence_ref, str):
                    evidence_paths.add(Path(evidence_ref).resolve(strict=False))
        check_items = [
            item for item in inventory.get("check_runs", [])
            if isinstance(item, dict)
            and Path(str(item.get("path", ""))).name.startswith("check-")
            and (Path(str(item.get("path", ""))) / "companion-manifest.json").is_file()
        ]
        check_items.sort(key=lambda item: _path_newness(Path(str(item["path"]))))
        retained = set(str(item["path"]) for item in check_items[-keep_checks:])
        for item in check_items:
            path = Path(str(item["path"])).resolve(strict=False)
            if any(evidence == path or path in evidence.parents for evidence in evidence_paths):
                actions.append({"path": str(path), "action": "kept", "reason": "kept: item evidence"})
            elif str(path) in retained:
                actions.append({"path": str(path), "action": "kept", "reason": f"kept: within newest {keep_checks}"})
            else:
                shutil.rmtree(path)
                actions.append({"path": str(path), "action": "removed", "reason": f"aged check run beyond newest {keep_checks}"})
    prune_error = None
    if prune_needed:
        completed = _git(repo, ["worktree", "prune"])
        if completed.returncode != 0:
            prune_error = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
    result = dict(inventory)
    result["mode"] = "apply"
    result["actions"] = actions
    if prune_error is not None:
        result["prune_error"] = prune_error
    return result


def render_tidy_text(payload: dict[str, Any]) -> str:
    lines = [f"ORRO tidy ({payload['mode']})", "Run worktrees:"]
    worktrees = payload.get("worktrees")
    if not isinstance(worktrees, list) or not worktrees:
        lines.append("  (none)")
    else:
        actions = {item["path"]: item for item in payload.get("actions", [])}
        for item in worktrees:
            lines.append(
                f"- {item['path']} branch={item.get('branch') or '-'} "
                f"base={item.get('base_commit') or '-'} head={item.get('head_commit') or '-'} "
                f"dirty={str(item.get('dirty')).lower()} size={item.get('size_bytes', 0)} "
                f"state={item.get('run_state')}"
            )
            action = actions.get(item["path"])
            if action is None:
                lines.append("  kept: dry-run")
            elif action["action"] == "kept":
                lines.append(f"  kept: {action['reason']}")
            else:
                lines.append(f"  {action['action']}: {action['reason']}")
    lines.append("Task worktrees:")
    task_worktrees = payload.get("task_worktrees")
    if not isinstance(task_worktrees, list) or not task_worktrees:
        lines.append("  (none)")
    else:
        actions = {item["path"]: item for item in payload.get("actions", [])}
        for item in task_worktrees:
            lines.append(
                f"- {item['path']} branch={item.get('branch') or '-'} "
                f"dirty={str(item.get('dirty')).lower()} item={item.get('item_id')} "
                f"status={item.get('item_status')}"
            )
            action = actions.get(item["path"])
            if action is None:
                lines.append("  kept: dry-run")
            elif action["action"] == "kept":
                lines.append(f"  kept: {action['reason']}")
            else:
                lines.append(f"  {action['action']}: {action['reason']}")
    lines.append("Registered outside runs:")
    outside = payload.get("registered_outside_runs")
    if not isinstance(outside, list) or not outside:
        lines.append("  (none)")
    else:
        actions = {item["path"]: item for item in payload.get("actions", [])}
        for item in outside:
            lines.append(
                f"- {item['path']} branch={item.get('branch') or '-'} "
                f"head={item.get('head_commit') or '-'} exists={str(item['exists']).lower()}"
            )
            action = actions.get(item["path"])
            if action is None:
                lines.append("  kept: dry-run")
            elif action["action"] == "kept":
                lines.append(f"  kept: {action['reason']}")
            else:
                lines.append(f"  {action['action']}: {action['reason']}")
    if payload.get("prune_error"):
        lines.append(f"Prune error: {payload['prune_error']}")
    lines.append(f"Boundary: {payload['boundary']}")
    return "\n".join(lines)


def _status_run(run_dir: Path, *, home: Path) -> dict[str, Any]:
    if (run_dir / "companion-manifest.json").is_file():
        state, evidence_ref = _companion_status(run_dir)
        try:
            binding = read_roadmap_binding(run_dir)
        except OrroRoadmapError:
            binding = None
        result = {
            "run_dir": str(run_dir),
            "state": state,
            "item_id": binding.get("item_id") if binding is not None else None,
            "step_id": binding.get("step_id") if binding is not None else None,
        }
        if evidence_ref is not None:
            result["evidence_ref"] = evidence_ref
        return result

    _, decision = decide_next(run_dir, home=home)
    try:
        binding = read_roadmap_binding(run_dir)
    except OrroRoadmapError:
        binding = None
    return {
        "run_dir": str(run_dir),
        "state": str(decision.get("decision", "blocked")),
        "item_id": binding.get("item_id") if binding is not None else None,
        "step_id": binding.get("step_id") if binding is not None else None,
    }


def _companion_status(run_dir: Path) -> tuple[str, str | None]:
    manifest_path = run_dir / "companion-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "companion-unverified", None
    if not isinstance(manifest, dict):
        return "companion-unverified", None

    verdict_ref = manifest.get("verdict_ref")
    if not isinstance(verdict_ref, dict):
        return "companion-unverified", None
    verdict_path_value = verdict_ref.get("path")
    verdict_hash = verdict_ref.get("sha256")
    if not isinstance(verdict_path_value, str) or not isinstance(verdict_hash, str):
        return "companion-unverified", None

    verdict_path = Path(verdict_path_value).resolve(strict=False)
    try:
        if not verdict_path.is_file() or _hash_file(verdict_path) != verdict_hash:
            return "companion-unverified", None
    except (OSError, ValueError, RuntimeError):
        return "companion-unverified", None

    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "companion-unverified", None
    if not isinstance(verdict, dict):
        return "companion-unverified", None

    if verdict.get("decision") == "pass":
        return "companion-pass", str(verdict_path)
    return "companion-blocked", None


def _roadmap_item_status(
    item: dict[str, Any], bound_runs: list[dict[str, Any]], *, repo: Path | None = None
) -> dict[str, Any]:
    result = dict(item)
    if "steps" in item:
        return _roadmap_item_steps_status(item, bound_runs, repo=repo)
    ordered = sorted(
        bound_runs,
        key=lambda run: _path_newness(Path(run["run_dir"])),
        reverse=True,
    )
    verified = next(
        (
            run
            for run in ordered
            if run["state"] in {"complete", "ready-for-handoff", "companion-pass"}
        ),
        None,
    )
    if verified is not None:
        result.update(
            {
                "status": "done (verified)",
                "evidence_ref": verified.get(
                    "evidence_ref",
                    str(Path(verified["run_dir"]) / "proofcheck-verdict.json"),
                ),
                "run_dir": verified["run_dir"],
                "run_state": verified["state"],
            }
        )
    elif ordered:
        result.update(
            {
                "status": "in-progress",
                "latest_run": ordered[0]["run_dir"],
                "run_state": ordered[0]["state"],
            }
        )
    elif item.get("status") == "done":
        result["status"] = "marked-done (unverified)"
    else:
        result["status"] = "not-started"
    return result


def _task_workspace_status(repo: Path, item_id: str) -> str | None:
    path = task_worktree_path(repo, item_id)
    if not path.is_dir():
        return None
    records = [record for record in discover_task_workspaces(repo) if record["path"] == path.resolve(strict=False)]
    if not records or not records[0]["valid"]:
        return "unverified descriptor"
    live = _live_worktree_state(path)
    state = "dirty" if live["dirty"] is True else "clean" if live["dirty"] is False else "unverified descriptor"
    return f".orro/worktrees/{item_id} (branch orro/{item_id}, {state})"


def _task_tidy_record(record: dict[str, Any], *, item_status: str) -> dict[str, Any]:
    path = Path(record["path"])
    live = _live_worktree_state(path)
    descriptor = record.get("descriptor") or {}
    return {
        "kind": "task",
        "path": str(path),
        "item_id": record.get("item_id"),
        "descriptor_valid": bool(record.get("valid")),
        "item_status": item_status,
        "branch": live.get("branch") or descriptor.get("branch"),
        "base_commit": descriptor.get("base_commit"),
        "head_commit": live.get("head_commit"),
        "dirty": live.get("dirty"),
        "dirty_error": live.get("error"),
        "size_bytes": _tree_size(path),
    }


def _roadmap_item_steps_status(
    item: dict[str, Any], bound_runs: list[dict[str, Any]], *, repo: Path | None = None
) -> dict[str, Any]:
    result = dict(item)
    step_records: list[dict[str, Any]] = []
    for step in item.get("steps", []):
        step_runs = [run for run in bound_runs if run.get("step_id") == step["id"]]
        ordered = sorted(
            step_runs,
            key=lambda run: _path_newness(Path(run["run_dir"])),
            reverse=True,
        )
        verified = next((run for run in ordered if _run_is_verified(run)), None)
        record: dict[str, Any] = {"id": step["id"], "state": "not-started"}
        if verified is not None:
            record.update(
                {
                    "state": "done (verified)",
                    "run_dir": verified["run_dir"],
                    "evidence_ref": verified.get(
                        "evidence_ref",
                        str(Path(verified["run_dir"]) / "proofcheck-verdict.json"),
                    ),
                }
            )
        elif ordered:
            record.update(
                {
                    "state": "in-progress",
                    "run_dir": ordered[0]["run_dir"],
                    "run_state": ordered[0]["state"],
                }
            )
        step_records.append(record)

    verified_count = sum(step["state"] == "done (verified)" for step in step_records)
    result["steps"] = step_records
    if not step_records:
        result["status"] = "not-started"
        result["next_step"] = None
        return result
    next_index = next(
        (index for index, step in enumerate(step_records) if step["state"] != "done (verified)"),
        None,
    )
    for index, record in enumerate(step_records):
        record["suggested_next_command"] = (
            _suggested_step_command(
                item,
                item["steps"][next_index],
                repo=str(repo) if repo else None,
            )
            if index == next_index
            else None
        )
    if verified_count == len(step_records):
        result["status"] = "done (verified)"
        result["next_step"] = None
    elif verified_count == 0 and not any("run_dir" in step for step in step_records):
        result["status"] = "not-started"
        result["next_step"] = _next_step_record(item, next_index, repo=repo)
    else:
        result["status"] = f"in-progress ({verified_count}/{len(step_records)} steps)"
        result["next_step"] = _next_step_record(item, next_index, repo=repo)
    return result


def _run_is_verified(run: dict[str, Any]) -> bool:
    return run["state"] in {"complete", "ready-for-handoff", "companion-pass"}


def _next_step_record(item: dict[str, Any], index: int | None, *, repo: Path | None = None) -> dict[str, Any] | None:
    if index is None:
        return None
    step = item["steps"][index]
    return {
        "id": step["id"],
        "suggested_next_command": _suggested_step_command(item, step, repo=str(repo) if repo else None),
    }


def _suggested_step_command(item: dict[str, Any], step: dict[str, Any], *, repo: str | None = None) -> str:
    repo_arg = repo or "<repo>"
    item_id, step_id = item["id"], step["id"]
    profile = step["profile"]
    checks = step.get("checks")
    if profile == "verification-only" and checks:
        return " ".join(
            ["orro check", *[f"--check '{check}'" for check in checks],
             f"--roadmap-item {item_id}", f"--roadmap-step {step_id}", f"--repo {repo_arg}"]
        )
    if profile == "code-change" and step.get("write_scope") and step.get("adapter"):
        command = f'orro flow "{item["title"]}: {step_id}"'
        for scope in step["write_scope"]:
            command += f" --write-scope '{scope}'"
        return f"{command} --adapter {step['adapter']} --roadmap-item {item_id} --roadmap-step {step_id} --repo {repo_arg}"
    return f"construct the command manually (profile: {profile})"


def _run_dirs(home: Path) -> list[Path]:
    runs = home / "runs"
    result = (
        sorted(
            (path.resolve(strict=False) for path in runs.iterdir() if path.is_dir()),
            key=_path_newness,
            reverse=True,
        )
        if runs.is_dir()
        else []
    )
    companion = home / "companion-run"
    if companion.is_dir():
        result.append(companion.resolve(strict=False))
    return result


def _path_newness(path: Path) -> tuple[int, str]:
    try:
        return path.stat().st_mtime_ns, path.name
    except OSError:
        return 0, path.name


def latest_run_dir(home: Path) -> Path | None:
    runs = home / "runs"
    if not runs.is_dir():
        return None
    candidates = [path.resolve(strict=False) for path in runs.iterdir() if path.is_dir()]
    return max(candidates, key=_path_newness) if candidates else None


def _run_worktree_paths(run_dirs: list[Path]) -> list[Path]:
    result: list[Path] = []
    for run_dir in run_dirs:
        worktrees = run_dir / "worktrees"
        if worktrees.is_dir():
            result.extend(
                path.resolve(strict=False)
                for path in worktrees.iterdir()
                if path.is_dir()
            )
    return result


def _worktree_receipts(run_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        for path in run_dir.glob("**/worktree-lane-receipt.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            worktree = payload.get("worktree") if isinstance(payload, dict) else None
            if isinstance(worktree, str):
                result[str(Path(worktree).resolve(strict=False))] = payload
    return result


def _status_worktree_dirty(path: Path, receipt: dict[str, Any] | None) -> bool:
    if receipt is not None and isinstance(receipt.get("dirty"), bool):
        return bool(receipt["dirty"])
    return _live_worktree_state(path)["dirty"] is True


def _tidy_worktree_record(
    *,
    path: Path,
    run_dir: Path,
    run_state: str,
    receipt: dict[str, Any] | None,
    registration: dict[str, Any] | None,
) -> dict[str, Any]:
    live = _live_worktree_state(path)
    head = live.get("head_commit") or (registration or {}).get("head_commit")
    base = receipt.get("base_commit") if receipt is not None else None
    return {
        "path": str(path),
        "run_dir": str(run_dir),
        "run_state": run_state,
        "exists": path.is_dir(),
        "registered": registration is not None,
        "branch": live.get("branch") or (registration or {}).get("branch"),
        "base_commit": base or head,
        "head_commit": head,
        "dirty": live["dirty"],
        "dirty_error": live.get("error"),
        "size_bytes": _tree_size(path),
    }


def _live_worktree_state(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {
            "branch": None,
            "head_commit": None,
            "dirty": None,
            "error": "path missing",
        }
    status = _git(
        path,
        [
            "status",
            "--porcelain",
            "--",
            ":(exclude).orro-task.json",
            ":(exclude)task-open-receipt.json",
            ".",
        ],
    )
    if status.returncode != 0:
        return {
            "branch": None,
            "head_commit": None,
            "dirty": None,
            "error": status.stderr.strip() or status.stdout.strip() or "git status failed",
        }
    branch = _git(path, ["branch", "--show-current"])
    head = _git(path, ["rev-parse", "HEAD"])
    return {
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "head_commit": head.stdout.strip() if head.returncode == 0 else None,
        "dirty": bool(status.stdout),
        "error": None,
    }


def _registered_worktrees(repo: Path) -> list[dict[str, Any]]:
    completed = _git(repo, ["worktree", "list", "--porcelain"])
    if completed.returncode != 0:
        return []
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*completed.stdout.splitlines(), ""]:
        if not line:
            if current.get("path"):
                records.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = str(Path(value).resolve(strict=False))
        elif key == "HEAD":
            current["head_commit"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "prunable":
            current["prunable"] = True
    return records


def _owning_run(path: Path, *, home: Path) -> Path | None:
    try:
        relative = path.resolve(strict=False).relative_to((home / "runs").resolve(strict=False))
    except ValueError:
        return None
    if len(relative.parts) < 3 or relative.parts[1] != "worktrees":
        return None
    return (home / "runs" / relative.parts[0]).resolve(strict=False)


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, directories, files in os.walk(path, onerror=lambda _exc: None):
        for name in [*directories, *files]:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def _git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
