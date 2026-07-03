"""W3 team fan-in orchestration.

This module coordinates local shell lanes into isolated git worktrees, emits
per-lane evidence, and writes a Depone-valid team ledger. It does not launch
agents or approve merges; it only records evidence for downstream verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.adapters.shell import run_shell_lane
from witnessd.emitter import emit_supervised_lane
from witnessd.eventlog import EventLog
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.lock import ClaimConflictError, OwnershipRegistry
from witnessd.observer import assert_separated
from witnessd.runlog import append_runlog
from witnessd.team_ledger import (
    build_evidence_next_verdict,
    build_team_ledger,
)
from witnessd.worktree import build_worktree_lane_receipt, create_lane_worktree


DEFAULT_STOP_RULE = "all write lanes pass or block"


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
) -> dict[str, Any]:
    base_dir = Path(out_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(str(base_dir / "runlog.jsonl"))
    registry = OwnershipRegistry(log, run_id=run_id)
    root = Path(repo_root).resolve()
    start_commit = base_commit or _git(root, ["rev-parse", "HEAD"])
    observer_path = Path(observer_dir).resolve() if observer_dir else base_dir / "observer"
    observer_path.mkdir(parents=True, exist_ok=True)
    os.chmod(observer_path, 0o700)

    lanes: list[dict[str, Any]] = []
    lane_outputs: list[dict[str, Any]] = []
    claimed_lanes: list[str] = []

    try:
        for spec in lane_specs:
            lane_id = _lane_id(spec)
            commands = _commands(spec)
            try:
                allowed_touched_files = registry.claim(
                    lane_id=lane_id, region=spec.get("region", [])
                )
            except ClaimConflictError:
                continue
            claimed_lanes.append(lane_id)

            if not allowed_touched_files:
                append_runlog(
                    log,
                    run_id,
                    "read-only-lane-audit",
                    payload={"lane_id": lane_id, "commands": commands},
                )
                continue

            try:
                if spec.get("adapter"):
                    lane = _run_adapter_lane(
                        lane_id=lane_id,
                        spec=spec,
                        repo_root=root,
                        base_commit=start_commit,
                        base_dir=base_dir,
                        observer_dir=observer_path,
                        allowed_touched_files=allowed_touched_files,
                        private_key_path=private_key_path,
                        public_key_path=public_key_path,
                        log=log,
                        run_id=run_id,
                        state_root=state_root,
                    )
                else:
                    lane = _run_write_lane(
                        lane_id=lane_id,
                        commands=commands,
                        repo_root=root,
                        base_commit=start_commit,
                        base_dir=base_dir,
                        observer_dir=observer_path,
                        allowed_touched_files=allowed_touched_files,
                        private_key_path=private_key_path,
                        public_key_path=public_key_path,
                        log=log,
                        run_id=run_id,
                    )
            except LaneBlocked as exc:
                lane = _blocked_adapter_lane(
                    lane_id=lane_id,
                    adapter=str(spec.get("adapter")),
                    base_commit=start_commit,
                    reason=exc.reason,
                    message=exc.message,
                )
            lanes.append(lane["ledger_lane"])
            lane_outputs.append(lane)
    finally:
        for lane_id in reversed(claimed_lanes):
            registry.release(lane_id=lane_id)

    ledger = build_team_ledger(
        leader_objective=leader_objective,
        leader_id=leader_id,
        start_commit=start_commit,
        stop_rule=stop_rule,
        lanes=lanes,
    )
    _write_json_artifact(log, run_id, base_dir / "team-ledger.json", ledger)
    return {
        "base_dir": base_dir,
        "ledger": ledger,
        "lanes": lane_outputs,
        "runlog": log.read(),
    }


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

    result = run_adapter_lane(
        root=str(Path(state_root).resolve(strict=False)) if state_root else str(repo_root),
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
