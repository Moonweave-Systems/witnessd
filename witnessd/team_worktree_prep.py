"""Prepare local team worktrees without launching worker agents."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from witnessd.canonical import canonical_hash

TEAM_LAUNCH_PREFLIGHT_KIND = "depone-team-launch-preflight"
TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION = "0.1"

TEAM_WORKTREE_PREP_KIND = "depone-team-worktree-prep"
TEAM_WORKTREE_PREP_SCHEMA_VERSION = "0.1"
TEAM_WORKTREE_PREP_DEPRECATION = {
    "status": "deprecated",
    "migration_target": "witnessd",
    "reason": "local worktree preparation mutates runtime state and belongs to witnessd",
}


class TeamWorktreePrepError(ValueError):
    """Structured team worktree preparation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def build_team_worktree_prep(
    preflight: dict[str, object],
    *,
    repo_root: Path,
    worktree_root: Path,
    create_worktree: bool = False,
) -> dict[str, object]:
    """Create or select per-lane worktrees and return a launch-prep receipt."""

    repo = _repo_root(repo_root)
    root = worktree_root.resolve(strict=False)
    errors: list[dict[str, str]] = []
    lanes: list[dict[str, object]] = []

    if not isinstance(preflight, dict):
        preflight = {}
        errors.append(_error("ERR_TEAM_WORKTREE_PREP_PREFLIGHT_INVALID", "preflight must be an object"))
    elif preflight.get("kind") != TEAM_LAUNCH_PREFLIGHT_KIND:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_PREFLIGHT_KIND_INVALID",
                f"preflight kind must be {TEAM_LAUNCH_PREFLIGHT_KIND}",
            )
        )
    elif preflight.get("schema_version") != TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_PREFLIGHT_SCHEMA_INVALID",
                f"preflight schema_version must be {TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION}",
            )
        )

    if preflight.get("decision") != "pass":
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_PREFLIGHT_NOT_PASS",
                "team launch preflight decision must be pass",
            )
        )
    errors.extend(_validate_team_launch_preflight(preflight))

    base_commit = preflight.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_BASE_COMMIT_REQUIRED",
                "preflight base_commit must be a non-empty string",
            )
        )
        base_commit = ""
    elif not _commit_exists(repo, base_commit):
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_BASE_COMMIT_INVALID",
                "preflight base_commit must resolve to a commit",
            )
        )

    raw_lanes = preflight.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_LANES_INVALID",
                "preflight lanes must be a non-empty list",
            )
        )
        raw_lanes = []

    planned_lanes = _planned_lanes(raw_lanes, root, errors)
    if errors:
        lanes = [_blocked_lane(lane, "blocked") for lane in planned_lanes]
        return _payload(preflight, repo, root, create_worktree, False, lanes, errors)

    created_any = False
    for lane in planned_lanes:
        lane_errors: list[dict[str, str]] = []
        target = lane["resolved_worktree"]
        assert isinstance(target, Path)
        exists_before = target.exists()
        action = "selected"

        if not exists_before:
            if not create_worktree:
                lane_errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_CREATE_FLAG_REQUIRED",
                        "planned worktree is missing; pass --create-worktree to create it",
                        lane_id=str(lane["lane_id"]),
                    )
                )
                action = "blocked"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _git(repo, ["worktree", "add", "--detach", str(target), str(base_commit)])
                    created_any = True
                    action = "created"
                except TeamWorktreePrepError as exc:
                    lane_errors.append(_error(exc.code, exc.message, lane_id=str(lane["lane_id"])))
                    action = "blocked"

        head_commit = None
        branch = None
        exists_after = target.exists()
        if exists_after and not lane_errors:
            try:
                head_commit = _git(target, ["rev-parse", "HEAD"])
                branch = _git(target, ["branch", "--show-current"]) or _git(
                    target,
                    ["rev-parse", "--abbrev-ref", "HEAD"],
                )
            except TeamWorktreePrepError as exc:
                lane_errors.append(_error(exc.code, exc.message, lane_id=str(lane["lane_id"])))
                action = "blocked"
            if head_commit != base_commit:
                lane_errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_HEAD_COMMIT_MISMATCH",
                        "prepared worktree HEAD must match preflight base_commit",
                        lane_id=str(lane["lane_id"]),
                    )
                )
                action = "blocked"

        errors.extend(lane_errors)
        lanes.append(
            {
                "lane_id": lane["lane_id"],
                "planned_worktree": lane["planned_worktree"],
                "worktree_path": target.as_posix(),
                "action": action,
                "exists_before": exists_before,
                "exists_after": exists_after,
                "base_commit": base_commit,
                "head_commit": head_commit,
                "branch": branch,
                "evidence_dir": lane["evidence_dir"],
                "worktree_receipt": lane["worktree_receipt"],
                "errors": lane_errors,
            }
        )

    payload = _payload(preflight, repo, root, create_worktree, created_any, lanes, errors)
    validation_errors = validate_team_worktree_prep(payload)
    if validation_errors:
        payload["decision"] = "blocked"
        payload["errors"] = [*errors, *validation_errors]
    return payload


def validate_team_worktree_prep(payload: dict[str, object]) -> list[dict[str, str]]:
    """Return structured validation errors for a team worktree prep receipt."""

    errors: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return [_error("ERR_TEAM_WORKTREE_PREP_PAYLOAD_INVALID", "payload must be an object")]
    if payload.get("kind") != TEAM_WORKTREE_PREP_KIND:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_KIND_INVALID",
                f"kind must be {TEAM_WORKTREE_PREP_KIND}",
            )
        )
    if payload.get("schema_version") != TEAM_WORKTREE_PREP_SCHEMA_VERSION:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_SCHEMA_VERSION_INVALID",
                f"schema_version must be {TEAM_WORKTREE_PREP_SCHEMA_VERSION}",
            )
        )
    if payload.get("decision") not in {"pass", "blocked"}:
        errors.append(
            _error("ERR_TEAM_WORKTREE_PREP_DECISION_INVALID", "decision must be pass or blocked")
        )
    raw_errors = payload.get("errors")
    if not isinstance(raw_errors, list) or not all(isinstance(item, dict) for item in raw_errors):
        errors.append(_error("ERR_TEAM_WORKTREE_PREP_ERRORS_INVALID", "errors must be a list of objects"))
    elif payload.get("decision") == "pass" and raw_errors:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_DECISION_INVALID",
                "passing worktree prep must not include errors",
            )
        )
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        errors.append(_error("ERR_TEAM_WORKTREE_PREP_LANES_INVALID", "lanes must be a list"))
    else:
        for index, lane in enumerate(lanes):
            if not isinstance(lane, dict):
                errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_LANE_INVALID",
                        f"lanes[{index}] must be an object",
                    )
                )
                continue
            if lane.get("action") not in {"created", "selected", "blocked"}:
                errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_LANE_ACTION_INVALID",
                        "lane action must be created, selected, or blocked",
                        lane_id=str(lane.get("lane_id") or "<missing>"),
                    )
                )
            if not isinstance(lane.get("lane_id"), str) or not str(lane.get("lane_id")).strip():
                errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_LANE_ID_REQUIRED",
                        "lane_id must be a non-empty string",
                    )
                )
    boundary = payload.get("boundary")
    if not isinstance(boundary, dict):
        errors.append(_error("ERR_TEAM_WORKTREE_PREP_BOUNDARY_INVALID", "boundary must be an object"))
    else:
        for key in (
            "launches_agents",
            "executes_lane_commands",
            "calls_live_models",
            "raises_assurance",
            "deletes_worktrees",
        ):
            if boundary.get(key) is not False:
                errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_BOUNDARY_INVALID",
                        f"boundary.{key} must be false",
                    )
                )
        for key in ("create_worktree_requested", "runs_git_worktree_add"):
            if not isinstance(boundary.get(key), bool):
                errors.append(
                    _error(
                        "ERR_TEAM_WORKTREE_PREP_BOUNDARY_INVALID",
                        f"boundary.{key} must be a boolean",
                    )
                )
    return errors


def _payload(
    preflight: dict[str, object],
    repo: Path,
    worktree_root: Path,
    create_worktree: bool,
    created_any: bool,
    lanes: list[dict[str, object]],
    errors: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "kind": TEAM_WORKTREE_PREP_KIND,
        "schema_version": TEAM_WORKTREE_PREP_SCHEMA_VERSION,
        "decision": "blocked" if errors else "pass",
        "repo_root": repo.as_posix(),
        "worktree_root": worktree_root.as_posix(),
        "base_commit": preflight.get("base_commit"),
        "lane_count": len(lanes),
        "lanes": lanes,
        "errors": errors,
        "source_hashes": {"team_launch_preflight": canonical_hash(preflight)},
        "deprecation": dict(TEAM_WORKTREE_PREP_DEPRECATION),
        "boundary": {
            "create_worktree_requested": create_worktree,
            "runs_git_worktree_add": created_any,
            "launches_agents": False,
            "executes_lane_commands": False,
            "calls_live_models": False,
            "raises_assurance": False,
            "deletes_worktrees": False,
        },
    }


def _planned_lanes(
    raw_lanes: list[object],
    worktree_root: Path,
    errors: list[dict[str, str]],
) -> list[dict[str, object]]:
    lanes: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw_lane in enumerate(raw_lanes):
        if not isinstance(raw_lane, dict):
            errors.append(
                _error("ERR_TEAM_WORKTREE_PREP_LANE_INVALID", f"lanes[{index}] must be an object")
            )
            continue
        lane_id = raw_lane.get("lane_id")
        if not isinstance(lane_id, str) or not lane_id.strip():
            errors.append(_error("ERR_TEAM_WORKTREE_PREP_LANE_ID_REQUIRED", "lane_id is required"))
            lane_id = f"<missing-{index}>"
        elif lane_id in seen:
            errors.append(
                _error(
                    "ERR_TEAM_WORKTREE_PREP_LANE_ID_DUPLICATE",
                    "lane_id must be unique",
                    lane_id=lane_id,
                )
            )
        seen.add(str(lane_id))
        planned_worktree = raw_lane.get("planned_worktree")
        resolved_worktree: Path | None = None
        if not isinstance(planned_worktree, str) or not planned_worktree.strip():
            errors.append(
                _error(
                    "ERR_TEAM_WORKTREE_PREP_PATH_INVALID",
                    "planned_worktree must be a non-empty relative path",
                    lane_id=str(lane_id),
                )
            )
            planned_worktree = ""
        else:
            resolved_worktree = _resolve_planned_worktree(
                planned_worktree,
                worktree_root,
                errors,
                lane_id=str(lane_id),
            )
        lanes.append(
            {
                "lane_id": lane_id,
                "planned_worktree": planned_worktree,
                "resolved_worktree": resolved_worktree or worktree_root / "__invalid__",
                "evidence_dir": raw_lane.get("evidence_dir"),
                "worktree_receipt": raw_lane.get("worktree_receipt"),
            }
        )
    return lanes


def _blocked_lane(lane: dict[str, object], action: str) -> dict[str, object]:
    target = lane.get("resolved_worktree")
    return {
        "lane_id": lane.get("lane_id"),
        "planned_worktree": lane.get("planned_worktree"),
        "worktree_path": target.as_posix() if isinstance(target, Path) else None,
        "action": action,
        "exists_before": False,
        "exists_after": False,
        "base_commit": None,
        "head_commit": None,
        "branch": None,
        "evidence_dir": lane.get("evidence_dir"),
        "worktree_receipt": lane.get("worktree_receipt"),
        "errors": [],
    }


def _resolve_planned_worktree(
    planned_worktree: str,
    worktree_root: Path,
    errors: list[dict[str, str]],
    *,
    lane_id: str,
) -> Path:
    path = PurePosixPath(planned_worktree)
    if path.is_absolute() or ".." in path.parts:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_PATH_INVALID",
                "planned_worktree must stay relative to worktree_root",
                lane_id=lane_id,
            )
        )
        return worktree_root / "__invalid__"
    resolved = (worktree_root / Path(path.as_posix())).resolve(strict=False)
    root = worktree_root.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(
            _error(
                "ERR_TEAM_WORKTREE_PREP_PATH_INVALID",
                "planned_worktree must stay under worktree_root",
                lane_id=lane_id,
            )
        )
    return resolved


def _repo_root(repo_root: Path) -> Path:
    try:
        return Path(_git(repo_root, ["rev-parse", "--show-toplevel"]))
    except TeamWorktreePrepError as exc:
        raise TeamWorktreePrepError(
            "ERR_TEAM_WORKTREE_PREP_REPO_MISSING",
            "repo must be an existing git repository",
        ) from exc


def _commit_exists(repo: Path, revision: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _git(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise TeamWorktreePrepError("ERR_TEAM_WORKTREE_PREP_GIT_FAILED", message)
    return completed.stdout.strip()


def _error(code: str, message: str, *, lane_id: str | None = None) -> dict[str, str]:
    record = {"code": code, "message": message}
    if lane_id is not None:
        record["lane_id"] = lane_id
    return record


def _validate_team_launch_preflight(payload: dict[str, object]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if payload.get("kind") != TEAM_LAUNCH_PREFLIGHT_KIND:
        errors.append(
            _error(
                "ERR_TEAM_LAUNCH_PREFLIGHT_KIND_INVALID",
                f"kind must be {TEAM_LAUNCH_PREFLIGHT_KIND}",
            )
        )
    if payload.get("schema_version") != TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION:
        errors.append(
            _error(
                "ERR_TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION_INVALID",
                f"schema_version must be {TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION}",
            )
        )
    if payload.get("decision") not in {"pass", "blocked"}:
        errors.append(
            _error(
                "ERR_TEAM_LAUNCH_PREFLIGHT_DECISION_INVALID",
                "decision must be pass or blocked",
            )
        )
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        errors.append(
            _error("ERR_TEAM_LAUNCH_PREFLIGHT_LANES_INVALID", "lanes must be a list")
        )
    lane_count = payload.get("lane_count")
    if isinstance(lanes, list) and lane_count != len(lanes):
        errors.append(
            _error(
                "ERR_TEAM_LAUNCH_PREFLIGHT_LANES_INVALID",
                "lane_count must match lanes length",
            )
        )
    boundary = payload.get("boundary")
    if not isinstance(boundary, dict):
        errors.append(
            _error(
                "ERR_TEAM_LAUNCH_PREFLIGHT_BOUNDARY_INVALID",
                "boundary must be an object",
            )
        )
    else:
        for key in (
            "launches_agents",
            "creates_worktrees",
            "executes_commands",
            "mutates_worktree",
            "calls_live_models",
            "raises_assurance",
        ):
            if boundary.get(key) is not False:
                errors.append(
                    _error(
                        "ERR_TEAM_LAUNCH_PREFLIGHT_BOUNDARY_INVALID",
                        f"boundary.{key} must be false",
                    )
                )
    return errors


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as temp_text:
        root = Path(temp_text)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "r@x.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "prep-self-test"], cwd=repo, check=True)
        (repo / "sample.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        base = _git(repo, ["rev-parse", "HEAD"])
        preflight: dict[str, object] = {
            "kind": TEAM_LAUNCH_PREFLIGHT_KIND,
            "schema_version": TEAM_LAUNCH_PREFLIGHT_SCHEMA_VERSION,
            "decision": "pass",
            "launch_intent": "plan-only",
            "base_commit": base,
            "lane_count": 1,
            "lanes": [
                {
                    "lane_id": "lane-1",
                    "planned_worktree": "lane-1",
                    "evidence_dir": "lane-1",
                    "worktree_receipt": "lane-1/worktree-receipt.json",
                }
            ],
            "boundary": {
                "launches_agents": False,
                "creates_worktrees": False,
                "executes_commands": False,
                "mutates_worktree": False,
                "calls_live_models": False,
                "raises_assurance": False,
            },
            "errors": [],
        }
        receipt = build_team_worktree_prep(
            preflight,
            repo_root=repo,
            worktree_root=root / "worktrees",
            create_worktree=True,
        )
        if receipt["decision"] != "pass":
            raise AssertionError(f"expected pass: {receipt['errors']}")
        if receipt["boundary"]["launches_agents"] is not False:
            raise AssertionError("worktree prep must not launch agents")
