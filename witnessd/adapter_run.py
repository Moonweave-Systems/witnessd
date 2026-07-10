"""W4 adapter lane orchestration."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from witnessd.adapters.claude import run_claude_lane
from witnessd.adapters.codex import run_codex_lane
from witnessd.adapters.opencode import run_opencode_lane
from witnessd.budget import BudgetExceededError, CostBreaker
from witnessd.emitter import emit_lane_evidence
from witnessd.eventlog import EventLog
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.observer import assert_separated
from witnessd.preflight import PreflightError, probe_adapter_capability
from witnessd.router import RouteExhaustedError, route_model
from witnessd.signing import gen_operator_keypair
from witnessd.state import StateNamespace
from witnessd.status import render_status


class LaneBlocked(RuntimeError):
    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(reason if not message else f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture(adapter: str, task_id: str, route_decision: dict[str, Any]) -> dict[str, Any]:
    invocation = build_shell_invocation(task_id)
    invocation["profile"] = "w4-adapter-run"
    invocation["route"] = {
        "tier": route_decision["tier"],
        "model": route_decision["model"],
        "degraded": route_decision["degraded"],
    }
    invocation["toolbelt"]["allowed_tools"] = [adapter]
    invocation["toolbelt"]["output_schema"] = "adapter-result-v1"
    invocation["instructions"] = "Run the adapter lane and emit normalized evidence."
    return build_reference_adapter_fixture(invocation)


def _run_adapter(
    *,
    adapter: str,
    sandbox: str,
    prompt: str,
    transcript_path: str,
    transcript_invocation_path: str | None,
    log_path: str,
    codex_binary: str,
    claude_binary: str,
    opencode_binary: str,
    timeout_seconds: int,
    codex_env: dict[str, str] | None = None,
    allowed_touched_files: list[str] | None = None,
) -> Any:
    if adapter == "codex":
        return run_codex_lane(
            sandbox=sandbox,
            prompt=prompt,
            codex_binary=codex_binary,
            transcript_path=transcript_path,
            transcript_invocation_path=transcript_invocation_path,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            env=codex_env,
            allowed_touched_files=allowed_touched_files,
        )
    if adapter == "claude":
        return run_claude_lane(
            sandbox=sandbox,
            prompt=prompt,
            claude_binary=claude_binary,
            transcript_path=transcript_path,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
        )
    if adapter == "opencode":
        return run_opencode_lane(
            sandbox=sandbox,
            prompt=prompt,
            opencode_binary=opencode_binary,
            transcript_path=transcript_path,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
        )
    raise LaneBlocked("preflight_blocked", f"unknown adapter: {adapter}")


def _git_diff_patch(worktree: str, touched_files: list[str]) -> str:
    if not touched_files:
        return ""
    repo = str(Path(worktree).resolve(strict=False))

    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

    inside = run_git(["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return ""

    patch_parts: list[str] = []
    tracked = run_git(["diff", "--no-ext-diff", "HEAD", "--", *touched_files])
    if tracked.returncode != 0:
        tracked = run_git(["diff", "--no-ext-diff", "--", *touched_files])
    if tracked.returncode == 0 and tracked.stdout:
        patch_parts.append(tracked.stdout)

    untracked = run_git(["ls-files", "--others", "--exclude-standard", "--", *touched_files])
    for relpath in [line for line in untracked.stdout.splitlines() if line]:
        diff = run_git(["diff", "--no-index", "--", "/dev/null", relpath])
        if diff.returncode in {0, 1} and diff.stdout:
            patch_parts.append(diff.stdout)

    return "".join(patch_parts)


def run_adapter_lane(
    *,
    root: str,
    adapter: str,
    task_id: str,
    prompt: str,
    arm: str,
    tier: str,
    is_supported: Callable[[str], bool],
    budget: dict[str, Any],
    sandbox: str | None = None,
    predicted_tokens: int = 0,
    predicted_usd: float = 0.0,
    depth: int = 1,
    codex_binary: str = "codex",
    claude_binary: str = "claude",
    opencode_binary: str = "opencode",
    timeout_seconds: int = 120,
    evidence_dir: str | None = None,
    state_root: str | None = None,
    private_key_path: str | None = None,
    public_key_path: str | None = None,
    allowed_touched_files: list[str] | None = None,
) -> dict[str, Any]:
    worktree = str(Path(sandbox or root).resolve(strict=False))

    try:
        probe_adapter_capability(
            adapter,
            repo=worktree,
            codex_binary=codex_binary,
            claude_binary=claude_binary,
            opencode_binary=opencode_binary,
            require_ready=True,
        )
    except PreflightError as exc:
        raise LaneBlocked("preflight_blocked", exc.message) from exc

    with StateNamespace(state_root or root) as namespace:
        log = EventLog(namespace.runlog_path)
        try:
            route_decision = route_model(
                task_id=task_id,
                tier=tier,
                log=log,
                is_supported=is_supported,
            )
        except RouteExhaustedError as exc:
            raise LaneBlocked("route_blocked", str(exc)) from exc

        breaker = CostBreaker(
            log=log,
            max_tokens=int(budget["max_tokens"]),
            max_usd=float(budget["max_usd"]),
            max_depth=int(budget["max_depth"]),
        )
        try:
            breaker.check_can_spawn(
                task_id=task_id,
                predicted_tokens=predicted_tokens,
                predicted_usd=predicted_usd,
                depth=depth,
            )
        except BudgetExceededError as exc:
            raise LaneBlocked("budget_exceeded", str(exc)) from exc

        if evidence_dir is None:
            task_dir = namespace.state_dir / "lanes" / task_id
            lane_evidence_dir = task_dir / "evidence"
        else:
            lane_evidence_dir = Path(evidence_dir).resolve(strict=False)
            task_dir = lane_evidence_dir.parent
        transcript_path = task_dir / "adapter-transcript.txt"
        transcript_invocation_path = os.path.relpath(transcript_path, task_dir).replace(
            os.sep, "/"
        )
        log_path = task_dir / "adapter-command.json"
        key_dir = namespace.state_dir / "keys"
        task_dir.mkdir(parents=True, exist_ok=True)
        lane_evidence_dir.mkdir(parents=True, exist_ok=True)
        key_dir.mkdir(parents=True, exist_ok=True)
        if private_key_path is None or public_key_path is None:
            private_key, public_key = gen_operator_keypair(str(key_dir))
        else:
            private_key, public_key = private_key_path, public_key_path

        assert_separated(worktree, str(lane_evidence_dir / "capture-manifest.json"))
        codex_env = namespace.codex_env() if adapter == "codex" else None
        adapter_result = _run_adapter(
            adapter=adapter,
            sandbox=worktree,
            prompt=prompt,
            transcript_path=str(transcript_path),
            transcript_invocation_path=transcript_invocation_path,
            log_path=str(log_path),
            codex_binary=codex_binary,
            claude_binary=claude_binary,
            opencode_binary=opencode_binary,
            timeout_seconds=timeout_seconds,
            codex_env=codex_env,
            allowed_touched_files=allowed_touched_files,
        )
        diff_patch = _git_diff_patch(worktree, adapter_result.touched_files)
        allowed_for_manifest = (
            list(allowed_touched_files)
            if allowed_touched_files is not None
            else adapter_result.touched_files
        )

        started_at = _now_iso()
        ended_at = _now_iso()
        emitted = emit_lane_evidence(
            {
                "command_receipts": adapter_result.command_receipts,
                "touched_files": adapter_result.touched_files,
                "test_output": adapter_result.test_output,
            },
            str(lane_evidence_dir),
            private_key,
            fixture=_fixture(adapter, task_id, route_decision),
            allowed_touched_files=allowed_for_manifest,
            public_key_path=public_key,
            task_id=task_id,
            invocation=adapter_result.invocation,
            runner_sandbox=worktree,
            runner_kind=adapter_result.runner_kind,
            started_at=started_at,
            ended_at=ended_at,
            diff_patch=diff_patch,
        )

        return {
            "runner_receipt": emitted["receipt"],
            "capture_manifest": emitted["manifest"],
            "bundle_path": str(lane_evidence_dir / "bundle.json"),
            "evidence_dir": str(lane_evidence_dir),
            "route": route_decision,
            "status_axis": {
                "assurance": render_status(pending=1, verdict=None),
                "lifecycle": "active",
            },
        }
