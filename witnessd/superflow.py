"""Thin Superflow scout artifact producer for witnessd."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from witnessd.canonical import canonical_hash

SCHEMA_VERSION = "1.0"


def run_scout(goal: str, *, repo: Path, home: Path, out_dir: Path | None = None) -> dict[str, Any]:
    """Create read-only Superflow scout artifacts for a repository."""

    repo = repo.resolve(strict=False)
    home = home.resolve(strict=False)
    run_dir = (
        out_dir.resolve(strict=False)
        if out_dir is not None
        else home / "runs" / f"scout-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    files = _repo_files(repo)
    branch = _git_stdout(repo, ["branch", "--show-current"]) or "unknown"
    head_commit = _git_stdout(repo, ["rev-parse", "HEAD"]) or "unknown"
    repo_profile = {
        "kind": "superflow-repo-profile",
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo),
        "branch": branch,
        "head_commit": head_commit,
        "files": files,
    }
    _write_json(run_dir / "repo-profile.json", repo_profile)

    selected_paths = _select_context_paths(goal, files)
    context_pack = {
        "kind": "superflow-context-pack",
        "schema_version": SCHEMA_VERSION,
        "repo_profile_hash": canonical_hash(repo_profile),
        "selected_paths": selected_paths,
        "reason": "read-only Superflow scout context selection",
    }
    _write_json(run_dir / "context-pack.json", context_pack)
    _write_discovery_notes(run_dir / "discovery-notes.md", goal, repo, selected_paths)
    _write_json(
        run_dir / "lane-context.json",
        {
            "kind": "superflow-lane-context",
            "schema_version": SCHEMA_VERSION,
            "goal": goal,
            "selected_paths": selected_paths,
            "allowed_terminal_state": "scouted",
        },
    )

    skillpack_lock = _build_skillpack_lock(repo, run_dir)
    _write_json(run_dir / "skillpack-lock.json", skillpack_lock)

    recipe = _build_verification_recipe()
    _write_json(run_dir / "verification-recipe.json", recipe)

    mcp_receipt = _build_fake_mcp_receipt(goal)
    _write_json(run_dir / "mcp-tool-receipt-fake.json", mcp_receipt)

    handoff = {
        "kind": "superflow-pr-handoff",
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "evidence_dir": str(run_dir),
        "changed_files": [],
        "verification_receipt_hashes": [],
        "mcp_tool_receipt_hashes": [canonical_hash(mcp_receipt)],
        "unresolved_risks": ["scout is planning evidence only; no commands were executed"],
        "human_required_actions": [
            "run proofrun or another witnessd execution step before proofcheck can pass"
        ],
        "approves_merge": False,
        "boundary": {"raises_assurance": False, "approves_merge": False},
    }
    _write_json(run_dir / "pr-handoff.json", handoff)

    return {
        "decision": "scouted",
        "run_dir": str(run_dir),
        "repo_profile": str(run_dir / "repo-profile.json"),
        "context_pack": str(run_dir / "context-pack.json"),
        "discovery_notes": str(run_dir / "discovery-notes.md"),
        "skillpack_lock": str(run_dir / "skillpack-lock.json"),
        "verification_recipe": str(run_dir / "verification-recipe.json"),
        "mcp_tool_receipt": str(run_dir / "mcp-tool-receipt-fake.json"),
        "pr_handoff": str(run_dir / "pr-handoff.json"),
    }


def _repo_files(repo: Path) -> list[str]:
    output = _git_stdout(repo, ["ls-files"])
    if output:
        return sorted(line for line in output.splitlines() if line)
    files: list[str] = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [name for name in dirs if name not in {".git", ".witnessd", "__pycache__"}]
        for name in names:
            path = Path(root) / name
            files.append(path.relative_to(repo).as_posix())
    return sorted(files)


def _select_context_paths(goal: str, files: list[str]) -> list[str]:
    preferred = [
        path
        for path in files
        if path in {"SPEC3.md", "README.md", "SKILL.md", "AGENTS.md"}
        or path.startswith("witnessd/")
        or path.startswith("tests/")
    ]
    tokens = {token.lower() for token in goal.replace("/", " ").replace("-", " ").split() if len(token) >= 4}
    matched = [
        path
        for path in files
        if any(token in path.lower() for token in tokens)
    ]
    selected: list[str] = []
    for path in [*preferred, *matched]:
        if path not in selected:
            selected.append(path)
        if len(selected) >= 24:
            break
    return selected


def _write_discovery_notes(path: Path, goal: str, repo: Path, selected_paths: list[str]) -> None:
    lines = [
        "# Superflow Scout Discovery Notes",
        "",
        f"- Goal: {goal}",
        f"- Repo: {repo}",
        "- Action 1: listed tracked repository files to build repo-profile.json.",
        "- Action 2: selected bounded context paths for context-pack.json.",
        "",
        "## Selected Paths",
        "",
    ]
    lines.extend(f"- `{item}`" for item in selected_paths)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_skillpack_lock(repo: Path, run_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    skillpack_dir = run_dir / "skillpacks"
    for source_name in ("SKILL.md", "AGENTS.md"):
        source = repo / source_name
        if not source.is_file():
            continue
        target = skillpack_dir / source_name.lower().replace(".md", "-copy.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        entries.append(
            {
                "path": target.relative_to(run_dir).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "frontmatter": _frontmatter(source.read_text(encoding="utf-8")),
            }
        )
    return {
        "kind": "superflow-skillpack-lock",
        "schema_version": SCHEMA_VERSION,
        "entries": entries,
    }


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip()
    return fields


def _build_verification_recipe() -> dict[str, Any]:
    return {
        "kind": "superflow-verification-recipe",
        "schema_version": SCHEMA_VERSION,
        "commands": [
            {
                "id": "scout-artifact-smoke",
                "argv": ["python3", "-m", "witnessd", "self-test", "--all"],
                "expected_exit_code": 0,
                "required": True,
            }
        ],
    }


def _build_fake_mcp_receipt(goal: str) -> dict[str, Any]:
    invocation = {"tool": "fake.repo_lookup", "args": {"goal": goal}}
    redacted_input = {"goal": goal}
    observed_output = {"mode": "fixture", "items": []}
    return {
        "kind": "superflow-mcp-tool-receipt",
        "schema_version": SCHEMA_VERSION,
        "tool_name": "fake.repo_lookup",
        "server_id": "fake-mcp",
        "invocation": invocation,
        "invocation_hash": canonical_hash(invocation),
        "redacted_input": redacted_input,
        "redacted_input_hash": canonical_hash(redacted_input),
        "observed_output": observed_output,
        "output_hash": canonical_hash(observed_output),
        "captured_at": "2026-07-04T00:00:00Z",
        "policy_flags": {"fake": True, "network": False, "raises_assurance": False},
    }


def _git_stdout(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
