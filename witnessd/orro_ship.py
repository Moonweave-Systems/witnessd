"""Evidence-gated ORRO push and pull-request shipping."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


SHIP_KIND = "orro-ship-receipt"
SHIP_SCHEMA_VERSION = "0.1"
BOUNDARY = {
    "merges": False,
    "approves_merge": False,
    "forced": False,
    "orchestration_metadata_not_proof": True,
}


def build_ship(
    run_dir: Path, *, home: Path, repo: Path, remote: str = "origin"
) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False)
    repo = repo.resolve(strict=False)
    blockers = _evidence_blockers(run_dir, home)
    remote_name = remote
    remote_url = _git(repo, "remote", "get-url", remote_name)
    if not blockers and _git(repo, "status", "--porcelain"):
        goal = _goal(run_dir)
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_WORKTREE_DIRTY",
                "the repository working tree is dirty; ship never commits",
                [f'git add -A && git commit -m "{goal}"'],
            )
        )
    if not blockers:
        branch = _git(repo, "branch", "--show-current")
        default_branch = _default_branch(repo, remote_name)
        if remote_url and not default_branch:
            blockers.append(
                _blocker(
                    "ERR_ORRO_SHIP_DEFAULT_BRANCH_UNKNOWN",
                    f"could not resolve {remote_name}/HEAD; ship refuses to guess the default branch",
                    [f"git remote set-head {remote_name} --auto"],
                )
            )
        elif default_branch and branch == default_branch:
            blockers.append(
                _blocker(
                    "ERR_ORRO_SHIP_DEFAULT_BRANCH",
                    f"ship refuses to push the default branch ({default_branch})",
                    [f"git checkout -b {shlex.quote(_suggested_branch(_goal(run_dir)))}"],
                )
            )
    if not remote_url:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_REMOTE_REQUIRED",
                f"git remote {remote_name!r} is missing; add a remote or pass --remote",
                [f"git remote add {remote_name} <url>"],
            )
        )
    if blockers:
        return 1, {
            "kind": "orro-ship",
            "run_dir": str(run_dir),
            "blocked": True,
            "blockers": blockers,
            "boundary": BOUNDARY,
        }
    return 0, {
        "kind": "orro-ship",
        "run_dir": str(run_dir),
        "blocked": False,
        "remote": remote_name,
        "branch": _git(repo, "branch", "--show-current"),
        "boundary": BOUNDARY,
    }


def ship_run(
    run_dir: Path,
    *,
    home: Path,
    repo: Path,
    remote: str = "origin",
    receipt_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False)
    repo = repo.resolve(strict=False)
    blockers = _evidence_blockers(run_dir, home)
    remote_url = _git(repo, "remote", "get-url", remote)
    if not remote_url:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_REMOTE_REQUIRED",
                f"git remote {remote!r} is missing; add a remote or pass --remote",
                [f"git remote add {remote} <url>"],
            )
        )
    if not blockers and _git(repo, "status", "--porcelain"):
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_WORKTREE_DIRTY",
                "the repository working tree is dirty; ship never commits",
                [f'git add -A && git commit -m "{_goal(run_dir)}"'],
            )
        )
    branch = _git(repo, "branch", "--show-current")
    default_branch = _default_branch(repo, remote)
    if not blockers and remote_url and not default_branch:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_DEFAULT_BRANCH_UNKNOWN",
                f"could not resolve {remote}/HEAD; ship refuses to guess the default branch",
                [f"git remote set-head {remote} --auto"],
            )
        )
    elif not blockers and default_branch and branch == default_branch:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_DEFAULT_BRANCH",
                f"ship refuses to push the default branch ({default_branch})",
                [f"git checkout -b {shlex.quote(_suggested_branch(_goal(run_dir)))}"],
            )
        )
    if blockers:
        return 1, {"kind": "orro-ship", "run_dir": str(run_dir), "blocked": True, "blockers": blockers, "boundary": BOUNDARY}

    subprocess.run(["git", "push", "-u", remote, branch], cwd=repo, check=True, env=os.environ.copy())
    goal = _goal(run_dir)
    verdict_hash = _hash_file(run_dir / "proofcheck-verdict.json")
    handoff_hash = _hash_file(run_dir / "orro-handoff.json")
    body = (
        f"ORRO guardrail receipt\n\nRun directory: {run_dir}\n"
        f"Proofcheck decision: pass\nProofcheck verdict sha256: {verdict_hash}\n"
        f"Handoff artifact sha256: {handoff_hash}\n\n"
        "This PR carries a guardrail receipt (observed run + verifier verdict). "
        "Merge approval stays human — shipping is not approval."
    )
    pr_args = ["gh", "pr", "create", "--title", goal, "--body", body]
    pr_command = " ".join(shlex.quote(part) for part in pr_args)
    pr_url = None
    gh_path = shutil.which("gh")
    if gh_path:
        completed = subprocess.run(pr_args, cwd=repo, check=False, capture_output=True, text=True, env=os.environ.copy())
        if completed.returncode == 0:
            pr_url = completed.stdout.strip() or None
    receipt = {
        "kind": SHIP_KIND,
        "schema_version": SHIP_SCHEMA_VERSION,
        "remote": remote,
        "branch": branch,
        "pushed": True,
        "pr_url": pr_url,
        "pr_command": None if pr_url else pr_command,
        "verdict_sha256": verdict_hash,
        "handoff_sha256": handoff_hash,
        "boundary": BOUNDARY,
    }
    target = (receipt_path or run_dir / "ship-receipt.json").resolve(strict=False)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0, {"kind": "orro-ship", "run_dir": str(run_dir), "blocked": False, "ship_receipt": receipt}


def _evidence_blockers(run_dir: Path, home: Path) -> list[dict[str, Any]]:
    verdict_path = run_dir / "proofcheck-verdict.json"
    handoff_path = run_dir / "orro-handoff.json"
    if not verdict_path.is_file():
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_REQUIRED", "a passing bound proofcheck-verdict.json is required", [f"python3 -m orro proofcheck {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(verdict_path))}"])]
    if not handoff_path.is_file():
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_REQUIRED", "orro-handoff.json is required after proofcheck", [f"python3 -m orro handoff {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(handoff_path))}"])]
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [_blocker("ERR_ORRO_SHIP_EVIDENCE_INVALID", "proofcheck and handoff artifacts must be readable JSON objects", [f"python3 -m orro handoff {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(handoff_path))}"])]
    if not isinstance(verdict, dict) or verdict.get("decision") != "pass":
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_NOT_PASS", "proofcheck-verdict.json decision must be pass", [f"python3 -m orro proofcheck {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(verdict_path))}"])]
    from witnessd.cli.verify import _proofcheck_binding

    if verdict.get("orro_binding") != _proofcheck_binding(run_dir):
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_UNBOUND", "proofcheck-verdict.json is not bound to this run", [f"python3 -m orro proofcheck {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(verdict_path))}"])]
    if not isinstance(handoff, dict) or handoff.get("kind") != "orro-handoff" or handoff.get("evidence_dir") != str(run_dir):
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_UNBOUND", "orro-handoff.json is not bound to this run", [f"python3 -m orro handoff {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(handoff_path))}"])]
    expected = _hash_file(verdict_path)
    if not any(isinstance(ref, dict) and ref.get("path") == "proofcheck-verdict.json" and ref.get("sha256") == expected and ref.get("decision") == "pass" for ref in handoff.get("decision_refs", [])):
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_STALE", "orro-handoff.json proofcheck reference is stale", [f"python3 -m orro handoff {shlex.quote(str(run_dir))} --home {shlex.quote(str(home))} --out {shlex.quote(str(handoff_path))}"])]
    return []


def _blocker(code: str, message: str, next_commands: list[str]) -> dict[str, Any]:
    return {"code": code, "message": message, "next_commands": next_commands}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _default_branch(repo: Path, remote: str) -> str | None:
    ref = _git(repo, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
    return ref.rsplit("/", 1)[-1] if ref else None


def _goal(run_dir: Path) -> str:
    for name in ("workflow-plan.json", "role-lane-plan.json"):
        try:
            payload = json.loads((run_dir / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("goal"), str) and payload["goal"]:
            return payload["goal"]
    return f"ship {run_dir.name}"


def _suggested_branch(goal: str) -> str:
    slug = "-".join(goal.lower().split())
    return f"feat/{slug[:60]}"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
