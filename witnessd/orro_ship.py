"""Evidence-gated ORRO push and pull-request shipping."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
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


def _cmd(*parts: object) -> str:
    return shlex.join([str(part) for part in parts])


def build_ship(
    run_dir: Path, *, home: Path, repo: Path, remote: str = "origin"
) -> tuple[int, dict[str, Any]]:
    run_dir = run_dir.resolve(strict=False)
    home = home.resolve(strict=False)
    repo = repo.resolve(strict=False)
    blockers = _evidence_blockers(run_dir, home, repo=repo)
    remote_name = remote
    remote_url = _git(repo, "remote", "get-url", remote_name)
    if not blockers and _git(repo, "status", "--porcelain"):
        blockers.append(_dirty_blocker(run_dir, repo, home))
    if not blockers:
        branch = _git(repo, "branch", "--show-current")
        if not branch:
            blockers.append(
                _blocker(
                    "ERR_ORRO_SHIP_BRANCH_REQUIRED",
                    "ship refuses to push from detached HEAD; check out a named branch",
                    [_cmd("git", "switch", "-c", _suggested_branch(_goal(run_dir)))],
                )
            )
        default_branch = _default_branch(repo, remote_name)
        if remote_url and not default_branch:
            blockers.append(
                _blocker(
                    "ERR_ORRO_SHIP_DEFAULT_BRANCH_UNKNOWN",
                    f"could not resolve {remote_name}/HEAD; ship refuses to guess the default branch",
                    [_cmd("git", "remote", "set-head", remote_name, "--auto")],
                )
            )
        elif default_branch and branch == default_branch:
            blockers.append(
                _blocker(
                    "ERR_ORRO_SHIP_DEFAULT_BRANCH",
                    f"ship refuses to push the default branch ({default_branch})",
                    [_cmd("git", "switch", "-c", _suggested_branch(_goal(run_dir)))],
                )
            )
    if not remote_url:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_REMOTE_REQUIRED",
                f"git remote {remote_name!r} is missing; add a remote or pass --remote",
                [_cmd("git", "remote", "add", remote_name, "<url>")],
            )
        )
    if blockers:
        return 1, _blocked_payload(run_dir, blockers)
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
    blockers = _evidence_blockers(run_dir, home, repo=repo)
    remote_url = _git(repo, "remote", "get-url", remote)
    if not remote_url:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_REMOTE_REQUIRED",
                f"git remote {remote!r} is missing; add a remote or pass --remote",
                [_cmd("git", "remote", "add", remote, "<url>")],
            )
        )
    if not blockers and _git(repo, "status", "--porcelain"):
        blockers.append(_dirty_blocker(run_dir, repo, home))
    branch = _git(repo, "branch", "--show-current")
    if not blockers and not branch:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_BRANCH_REQUIRED",
                "ship refuses to push from detached HEAD; check out a named branch",
                [_cmd("git", "switch", "-c", _suggested_branch(_goal(run_dir)))],
            )
        )
    default_branch = _default_branch(repo, remote)
    if not blockers and remote_url and not default_branch:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_DEFAULT_BRANCH_UNKNOWN",
                f"could not resolve {remote}/HEAD; ship refuses to guess the default branch",
                [_cmd("git", "remote", "set-head", remote, "--auto")],
            )
        )
    elif not blockers and default_branch and branch == default_branch:
        blockers.append(
            _blocker(
                "ERR_ORRO_SHIP_DEFAULT_BRANCH",
                f"ship refuses to push the default branch ({default_branch})",
                [_cmd("git", "switch", "-c", _suggested_branch(_goal(run_dir)))],
            )
        )
    if blockers:
        return 1, _blocked_payload(run_dir, blockers)

    observed_base_commit = _read_start_commit(run_dir)
    pushed_head_commit = _git(repo, "rev-parse", "HEAD")
    try:
        subprocess.run(
            ["git", "push", "-u", remote, branch],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        goal = _goal(run_dir)
        verdict_hash = _hash_file(run_dir / "proofcheck-verdict.json")
        handoff_hash = _hash_file(run_dir / "orro-handoff.json")
        body = (
            "ORRO guardrail receipt\n\n"
            f"Run directory: {run_dir.name}\n"
            f"Proofcheck decision: pass for the observed run (base {observed_base_commit}).\n"
            f"Observed base commit: {observed_base_commit}\n"
            f"Pushed head commit: {pushed_head_commit}\n"
            f"Proofcheck verdict sha256: {verdict_hash}\n"
            f"Handoff artifact sha256: {handoff_hash}\n\n"
            f"The pushed head {pushed_head_commit} descends from that base; commits added "
            "after the observed run are NOT covered by this verdict. Merge approval stays human."
        )
        pr_args = ["gh", "pr", "create", "--title", goal, "--body", body]
        pr_command = _cmd(*pr_args)
        pr_url = None
        gh_path = shutil.which("gh")
        if gh_path:
            completed = subprocess.run(
                pr_args, cwd=repo, check=False, capture_output=True, text=True, env=os.environ.copy()
            )
            if completed.returncode == 0:
                pr_url = completed.stdout.strip() or None
        receipt = {
            "kind": SHIP_KIND,
            "schema_version": SHIP_SCHEMA_VERSION,
            "remote": remote,
            "branch": branch,
            "pushed": True,
            "observed_base_commit": observed_base_commit,
            "pushed_head_commit": pushed_head_commit,
            "pr_url": pr_url,
            "pr_command": None if pr_url else pr_command,
            "verdict_sha256": verdict_hash,
            "handoff_sha256": handoff_hash,
            "boundary": BOUNDARY,
        }
        target = (receipt_path or run_dir / "ship-receipt.json").resolve(strict=False)
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        return 1, {
            "kind": "orro-ship",
            "run_dir": str(run_dir),
            "blocked": False,
            "pushed": True,
            "error": {
                "code": "ERR_ORRO_SHIP_POST_PUSH_FAILED",
                "message": str(exc),
            },
            "boundary": BOUNDARY,
        }
    return 0, {"kind": "orro-ship", "run_dir": str(run_dir), "blocked": False, "ship_receipt": receipt}


def _evidence_blockers(run_dir: Path, home: Path, *, repo: Path) -> list[dict[str, Any]]:
    verdict_path = run_dir / "proofcheck-verdict.json"
    handoff_path = run_dir / "orro-handoff.json"
    if verdict_path.is_symlink() or handoff_path.is_symlink():
        return [_blocker("ERR_ORRO_SHIP_EVIDENCE_INVALID", "ship refuses symlinked evidence artifacts", [])]
    if not verdict_path.is_file():
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_REQUIRED", "a passing bound proofcheck-verdict.json is required", [_cmd("/usr/bin/python3", "-m", "orro", "proofcheck", run_dir, "--home", home, "--out", verdict_path)])]
    if not handoff_path.is_file():
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_REQUIRED", "orro-handoff.json is required after proofcheck", [_cmd("/usr/bin/python3", "-m", "orro", "handoff", run_dir, "--home", home, "--out", handoff_path)])]
    if not any((run_dir / name).is_file() and not (run_dir / name).is_symlink() for name in ("team-ledger.json", "capture-manifest.json")):
        return [_blocker("ERR_ORRO_SHIP_RUN_EVIDENCE_MISSING", "ship requires persisted team-ledger or capture evidence", [_cmd("/usr/bin/python3", "-m", "orro", "proofcheck", run_dir, "--home", home, "--out", verdict_path)])]
    try:
        from witnessd.cli._output import _depone_subprocess_env

        env = _depone_subprocess_env(home)
        witnessd_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = f"{witnessd_root}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else witnessd_root
        with tempfile.TemporaryDirectory(prefix="orro-ship-proofcheck-") as scratch:
            fresh_verdict = Path(scratch) / "proofcheck-verdict.json"
            completed = subprocess.run(
                ["/usr/bin/python3", "-m", "orro", "proofcheck", str(run_dir), "--home", str(home), "--out", str(fresh_verdict), "--json"],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        if not completed.stdout.strip():
            raise ValueError(f"proofcheck exited {completed.returncode} without a JSON verdict: {completed.stderr.strip()[-200:]}")
        verifier = json.loads(completed.stdout)
    except Exception as exc:  # noqa: BLE001 - readiness and delegation must block ship
        return [_blocker("ERR_ORRO_SHIP_VERIFIER_UNAVAILABLE", f"fresh Depone proofcheck could not run: {exc}", [_cmd("/usr/bin/python3", "-m", "orro", "proofcheck", run_dir, "--home", home, "--out", verdict_path)])]
    if not isinstance(verifier, dict):
        return [_blocker("ERR_ORRO_SHIP_VERIFIER_UNAVAILABLE", "fresh Depone proofcheck did not return an object", [])]
    if completed.returncode != 0 or verifier.get("decision") != "pass":
        error = verifier.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        code = "ERR_ORRO_SHIP_VERIFIER_UNAVAILABLE" if error_code and "READINESS" in str(error_code) else "ERR_ORRO_SHIP_PROOFCHECK_NOT_PASS"
        return [_blocker(code, "fresh Depone proofcheck did not return a passing decision", [_cmd("/usr/bin/python3", "-m", "orro", "proofcheck", run_dir, "--home", home, "--out", verdict_path)])]
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [_blocker("ERR_ORRO_SHIP_EVIDENCE_INVALID", "proofcheck and handoff artifacts must be readable JSON objects", [])]
    if not isinstance(verdict, dict) or verdict.get("decision") != "pass":
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_NOT_PASS", "proofcheck-verdict.json decision must be pass", [])]
    from witnessd.cli.verify import _proofcheck_binding

    if verdict.get("orro_binding") != _proofcheck_binding(run_dir):
        return [_blocker("ERR_ORRO_SHIP_PROOFCHECK_UNBOUND", "proofcheck-verdict.json is not bound to this run", [])]
    if not isinstance(handoff, dict) or handoff.get("kind") != "orro-handoff" or handoff.get("evidence_dir") != str(run_dir):
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_UNBOUND", "orro-handoff.json is not bound to this run", [])]
    expected = _hash_file(verdict_path)
    if not any(isinstance(ref, dict) and ref.get("path") == "proofcheck-verdict.json" and ref.get("sha256") == expected and ref.get("decision") == "pass" for ref in handoff.get("decision_refs", [])):
        return [_blocker("ERR_ORRO_SHIP_HANDOFF_STALE", "orro-handoff.json proofcheck reference is stale", [])]
    try:
        start_commit = _read_start_commit(run_dir)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return [_blocker("ERR_ORRO_SHIP_EVIDENCE_LINEAGE_MISSING", "the run evidence does not contain a readable start_commit", [])]
    if not _git(repo, "cat-file", "-e", f"{start_commit}^{{commit}}") or _git(repo, "merge-base", "--is-ancestor", start_commit, "HEAD") == "":
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", start_commit, "HEAD"], cwd=repo, check=False, capture_output=True)
        if ancestor.returncode != 0:
            return [_blocker("ERR_ORRO_SHIP_EVIDENCE_REPO_MISMATCH", "the run observed a different repository or a branch that does not descend from the observed base", [_cmd("git", "log", "--oneline", "-1", start_commit)])]
    return []


def _read_start_commit(run_dir: Path) -> str:
    path = run_dir / "team-ledger.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("team-ledger.json is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    start_commit = payload.get("start_commit") if isinstance(payload, dict) else None
    if not isinstance(start_commit, str) or not start_commit:
        raise ValueError("team-ledger.json.start_commit is missing")
    return start_commit


def _dirty_blocker(run_dir: Path, repo: Path, home: Path) -> dict[str, Any]:
    paths = [line[3:] for line in _git(repo, "status", "--porcelain=v1").splitlines() if len(line) >= 4]
    home_rel = home.relative_to(repo).as_posix() if home == repo or repo in home.parents else None
    internal_prefixes = tuple(prefix for prefix in (home_rel, ".orro") if prefix)
    if paths and internal_prefixes and all(any(path == prefix or path.startswith(prefix + "/") for prefix in internal_prefixes) for path in paths):
        lines = [f"{home_rel}/" if home_rel else str(home), ".orro/"]
        return _blocker("ERR_ORRO_SHIP_WORKTREE_DIRTY", "only internal ORRO artifacts are dirty; add these paths to .gitignore before shipping", ["Add these exact lines to .gitignore:", *lines])
    return _blocker("ERR_ORRO_SHIP_WORKTREE_DIRTY", "the repository working tree is dirty; ship never commits", [_cmd("git", "add", "-A"), _cmd("git", "commit", "-m", _goal(run_dir))])


def _blocked_payload(run_dir: Path, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "orro-ship", "run_dir": str(run_dir), "blocked": True, "blockers": blockers, "boundary": BOUNDARY}


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
    if path.is_symlink():
        raise OSError(f"refusing to hash symlinked evidence artifact: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
