"""W4 adapter lane orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from witnessd.adapters.claude import run_claude_lane
from witnessd.adapters.codex import run_codex_lane
from witnessd.adapters.agy import run_agy_review_lane
from witnessd.adapters.gemini import run_gemini_review_lane
from witnessd.adapters.opencode import run_opencode_lane
from witnessd.budget import BudgetExceededError, CostBreaker
from witnessd.emitter import emit_lane_evidence
from witnessd.eventlog import EventLog
from witnessd.fixture import build_reference_adapter_fixture, build_shell_invocation
from witnessd.observer import assert_separated
from witnessd.preflight import PreflightError, probe_adapter_capability
from witnessd.privacy import (
    CAPTURE_PROFILE_FULL,
    CAPTURE_PROFILE_REDACTED,
    build_redaction_context,
    redact_value,
    validate_capture_profile,
)
from witnessd.router import RouteExhaustedError, route_model
from witnessd.runintent import (
    RUN_INTENT_ARTIFACT_NAME,
    build_run_intent,
    git_baseline,
    write_signed_run_intent,
)
from witnessd.signing import derive_public_key_id, gen_operator_keypair
from witnessd.state import StateNamespace
from witnessd.status import render_status


class LaneBlocked(RuntimeError):
    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(reason if not message else f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture(
    adapter: str, task_id: str, route_decision: dict[str, Any]
) -> dict[str, Any]:
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
    agy_binary: str,
    gemini_binary: str,
    opencode_binary: str,
    timeout_seconds: int,
    codex_env: dict[str, str] | None = None,
    allowed_touched_files: list[str] | None = None,
    approval_policy: str = "on-request",
    model: str | None = None,
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
            approval_policy=approval_policy,
            model=model,
        )
    if adapter == "claude":
        return run_claude_lane(
            sandbox=sandbox,
            prompt=prompt,
            claude_binary=claude_binary,
            transcript_path=transcript_path,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            model=model,
        )
    if adapter == "agy":
        return run_agy_review_lane(
            sandbox=sandbox,
            prompt=prompt,
            agy_binary=agy_binary,
            transcript_path=transcript_path,
            review_receipt_path=str(
                Path(transcript_path).with_name("review-receipt.json")
            ),
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            model=model,
        )
    if adapter == "gemini":
        return run_gemini_review_lane(
            sandbox=sandbox,
            prompt=prompt,
            gemini_binary=gemini_binary,
            transcript_path=transcript_path,
            review_receipt_path=str(
                Path(transcript_path).with_name("review-receipt.json")
            ),
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

    untracked = run_git(
        ["ls-files", "--others", "--exclude-standard", "--", *touched_files]
    )
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
    agy_binary: str = "agy",
    gemini_binary: str = "gemini",
    opencode_binary: str = "opencode",
    timeout_seconds: int = 120,
    evidence_dir: str | None = None,
    state_root: str | None = None,
    private_key_path: str | None = None,
    public_key_path: str | None = None,
    allowed_touched_files: list[str] | None = None,
    approval_policy: str = "on-request",
    capture_profile: str = CAPTURE_PROFILE_FULL,
    run_intent: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    capture_profile = validate_capture_profile(capture_profile)
    worktree = str(Path(sandbox or root).resolve(strict=False))

    try:
        probe_adapter_capability(
            adapter,
            repo=worktree,
            codex_binary=codex_binary,
            claude_binary=claude_binary,
            agy_binary=agy_binary,
            gemini_binary=gemini_binary,
            opencode_binary=opencode_binary,
            require_ready=True,
        )
    except PreflightError as exc:
        raise LaneBlocked("preflight_blocked", exc.message) from exc

    with StateNamespace(state_root or root) as namespace:
        # Fail closed if the runtime's own state dir (.witnessd, including
        # codex-home) would land inside the observed sandbox: a real codex
        # run writes cache/plugin/config files under codex-home, and those
        # would otherwise pollute the before/after touched_files diff with
        # witnessd's own runtime state rather than the agent's actual
        # changes (and would leave any seeded codex auth.json sitting inside
        # the agent-writable sandbox). This only fires when a caller passes
        # `root` equal to (or nesting inside) `sandbox` without a separate
        # `state_root` -- every real call site (team/fanin worktrees, the
        # CLI, existing tests) already keeps them apart.
        assert_separated(worktree, str(namespace.state_dir))
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

        allowed_for_manifest = list(allowed_touched_files or [])
        codex_env = namespace.codex_env() if adapter == "codex" else None
        redaction_context = None
        if capture_profile == CAPTURE_PROFILE_REDACTED:
            redaction_context = build_redaction_context(
                run_id=task_id,
                prompt=prompt,
                paths=allowed_for_manifest,
                worktree=worktree,
                env=codex_env,
            )
        redacted_allowed_for_manifest = list(
            redact_value(allowed_for_manifest, redaction_context)
        )
        if run_intent is None:
            run_intent = build_run_intent(
                run_id=task_id,
                baseline=git_baseline(worktree),
                allowed_paths=redacted_allowed_for_manifest,
                approval_policy=approval_policy,
                sandbox_mode="workspace-write" if adapter == "codex" else "unknown",
                provider=adapter,
                instruction_hashes={
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                },
                budgets={
                    "max_tokens": int(budget["max_tokens"]),
                    "max_usd": float(budget["max_usd"]),
                    "max_depth": int(budget["max_depth"]),
                    "predicted_tokens": int(predicted_tokens),
                    "predicted_usd": float(predicted_usd),
                    "depth": int(depth),
                    "timeout_seconds": int(timeout_seconds),
                },
                capture_profile=capture_profile,
            )
        run_intent_path = lane_evidence_dir / RUN_INTENT_ARTIFACT_NAME
        write_signed_run_intent(
            str(run_intent_path),
            run_intent,
            private_key,
            key_id=derive_public_key_id(public_key),
        )

        assert_separated(worktree, str(lane_evidence_dir / "capture-manifest.json"))
        adapter_result = _run_adapter(
            adapter=adapter,
            sandbox=worktree,
            prompt=prompt,
            transcript_path=str(transcript_path),
            transcript_invocation_path=transcript_invocation_path,
            log_path=str(log_path),
            codex_binary=codex_binary,
            claude_binary=claude_binary,
            agy_binary=agy_binary,
            gemini_binary=gemini_binary,
            opencode_binary=opencode_binary,
            timeout_seconds=timeout_seconds,
            codex_env=codex_env,
            allowed_touched_files=allowed_touched_files,
            approval_policy=approval_policy,
            model=model,
        )
        diff_patch = _git_diff_patch(worktree, adapter_result.touched_files)
        provider_artifacts = {}
        raw_events_path = getattr(adapter_result, "raw_events_path", None)
        normalized_events_path = getattr(adapter_result, "normalized_events_path", None)
        if raw_events_path is not None:
            provider_artifacts["events.raw"] = raw_events_path
        if normalized_events_path is not None:
            provider_artifacts["events.normalized"] = normalized_events_path
        review_receipt_path = getattr(adapter_result, "review_receipt_path", None)
        if review_receipt_path is not None:
            provider_artifacts["review-receipt"] = review_receipt_path
        model_declaration = getattr(adapter_result, "model_declaration", None)
        if model_declaration is not None:
            model_declaration_path = task_dir / "model-declaration.json"
            model_declaration_path.write_text(
                json.dumps(model_declaration, sort_keys=True), encoding="utf-8"
            )
            provider_artifacts["model-declaration"] = str(model_declaration_path)
        lane_result = {
            "command_receipts": adapter_result.command_receipts,
            "touched_files": adapter_result.touched_files,
            "test_output": adapter_result.test_output,
        }
        if redaction_context is not None:
            lane_result = redact_value(lane_result, redaction_context)
            diff_patch = str(redact_value(diff_patch, redaction_context))

        started_at = _now_iso()
        ended_at = _now_iso()
        emitted = emit_lane_evidence(
            lane_result,
            str(lane_evidence_dir),
            private_key,
            fixture=_fixture(adapter, task_id, route_decision),
            allowed_touched_files=redacted_allowed_for_manifest,
            public_key_path=public_key,
            task_id=task_id,
            invocation=redact_value(adapter_result.invocation, redaction_context),
            runner_sandbox=str(redact_value(worktree, redaction_context)),
            runner_kind=adapter_result.runner_kind,
            started_at=started_at,
            ended_at=ended_at,
            diff_patch=diff_patch,
            run_intent_path=str(run_intent_path),
            run_intent=run_intent,
            capture_profile=capture_profile,
            redaction_manifest=(
                redaction_context["manifest"] if redaction_context is not None else None
            ),
            provider_artifacts=provider_artifacts,
        )

        return {
            "runner_receipt": emitted["receipt"],
            "capture_manifest": emitted["manifest"],
            "bundle": emitted["bundle"],
            "bundle_path": str(lane_evidence_dir / "bundle.json"),
            "evidence_dir": str(lane_evidence_dir),
            "public_key_path": emitted["public_key_path"],
            "normalized_events": getattr(adapter_result, "normalized_events", []),
            "route": route_decision,
            "status_axis": {
                "assurance": render_status(pending=1, verdict=None),
                "lifecycle": "active",
            },
        }
