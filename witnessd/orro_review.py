"""ORRO advisory review-lane execution surface.

This module runs review-only role-lane plans through read-only adapter paths.
It is not proofrun, does not use team execution or region locks, does not verify
evidence, and cannot raise assurance.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from witnessd.adapters.agy import AgyAdapterError, run_agy_review_lane
from witnessd.adapters.base import AdapterExecutionError, AdapterResult
from witnessd.adapters.claude import (
    ClaudeAdapterError,
    run_claude_critic_lane,
)
from witnessd.adapters.gemini import GeminiAdapterError, run_gemini_review_lane
from witnessd.model_declaration import (
    VERIFICATION_REQUESTED_UNCONFIRMED,
    build_model_declaration,
)
from witnessd.orro_workflow import (
    OrroWorkflowError,
    validate_role_lane_plan,
    write_role_lane_plan_binding,
)

ERR_ORRO_REVIEW_PLAN_LOAD_FAILED = "ERR_ORRO_REVIEW_PLAN_LOAD_FAILED"
ERR_ORRO_REVIEW_PLAN_INVALID = "ERR_ORRO_REVIEW_PLAN_INVALID"
ERR_ORRO_REVIEW_LANE_UNSUPPORTED = "ERR_ORRO_REVIEW_LANE_UNSUPPORTED"
ERR_ORRO_REVIEW_WRITE_FAILED = "ERR_ORRO_REVIEW_WRITE_FAILED"


class OrroReviewError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def run_review_role_lane_plan(
    *,
    repo: Path,
    home: Path,
    role_lane_plan_path: Path,
    run_dir: Path | None = None,
    claude_binary: str = "claude",
    agy_binary: str = "agy",
    gemini_binary: str = "gemini",
    timeout_seconds: int = 120,
) -> tuple[int, dict[str, Any]]:
    """Run review-only lanes and emit advisory review receipts.

    The returned summary is witnessd-local advisory context only. It is not
    proof, proofrun execution evidence, Depone verifier truth, or assurance.
    """

    repo = repo.resolve(strict=False)
    home = home.resolve(strict=False)
    plan_source = role_lane_plan_path.resolve(strict=False)
    plan = _load_review_role_lane_plan(plan_source)
    out_dir = (
        run_dir.resolve(strict=False)
        if run_dir is not None
        else home
        / "runs"
        / f"review-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_root = _review_adapter_evidence_root(repo=repo, run_dir=out_dir)
    try:
        role_lane_plan_ref = write_role_lane_plan_binding(
            role_lane_plan=plan,
            source_path=plan_source,
            run_dir=out_dir,
        )
    except OrroWorkflowError as exc:
        raise OrroReviewError(exc.code, str(exc)) from exc

    lane_summaries: list[dict[str, Any]] = []
    for lane in plan["lanes"]:
        lane_summaries.append(
            _run_review_lane(
                lane,
                repo=repo,
                run_dir=out_dir,
                evidence_root=evidence_root,
                claude_binary=claude_binary,
                agy_binary=agy_binary,
                gemini_binary=gemini_binary,
                timeout_seconds=timeout_seconds,
            )
        )

    decision = (
        "pass"
        if lane_summaries and all(lane["exit_code"] == 0 for lane in lane_summaries)
        else "fail"
    )
    payload: dict[str, Any] = {
        "kind": "orro-review-summary",
        "schema_version": "1.0",
        "decision": decision,
        "run_dir": str(out_dir),
        "repo": str(repo),
        "workflow_profile": plan["workflow_profile"],
        "goal": plan["goal"],
        "role_lane_plan": role_lane_plan_ref,
        "lanes": lane_summaries,
        "can_change_evidence_verdict": False,
        "raises_assurance": False,
        "executes_proofrun": False,
        "verifies_evidence": False,
        "boundary": {
            "can_change_evidence_verdict": False,
            "raises_assurance": False,
            "executes_proofrun": False,
            "verifies_evidence": False,
            "review_receipts_are_assurance": False,
        },
    }
    _write_json(out_dir / "orro-review-summary.json", payload)
    return (0 if decision == "pass" else 1), payload


def _load_review_role_lane_plan(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroReviewError(ERR_ORRO_REVIEW_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroReviewError(
            ERR_ORRO_REVIEW_PLAN_INVALID, "role-lane plan must be a JSON object"
        )
    try:
        validate_role_lane_plan(payload)
    except OrroWorkflowError as exc:
        raise OrroReviewError(exc.code, str(exc)) from exc
    if payload.get("workflow_profile") not in {"review-only", "critic-only"}:
        raise OrroReviewError(
            ERR_ORRO_REVIEW_PLAN_INVALID,
            "orro review requires a review-only or critic-only role-lane plan",
        )
    if payload.get("execution_allowed") is not False:
        raise OrroReviewError(
            ERR_ORRO_REVIEW_PLAN_INVALID,
            "orro review does not accept executable role-lane plans",
        )
    lanes = payload.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise OrroReviewError(
            ERR_ORRO_REVIEW_PLAN_INVALID,
            "review-only role-lane plan has no reviewer lanes",
        )
    if payload.get("workflow_profile") == "critic-only" and len(lanes) != 1:
        raise OrroReviewError(
            ERR_ORRO_REVIEW_PLAN_INVALID,
            "critic-only role-lane plan must contain exactly one lane",
        )
    for lane in lanes:
        if not isinstance(lane, dict) or lane.get("phase") != "review":
            raise OrroReviewError(
                ERR_ORRO_REVIEW_PLAN_INVALID,
                "orro review accepts review lanes only",
            )
        if lane.get("may_execute") is not False or lane.get("raises_assurance") is not False:
            raise OrroReviewError(
                ERR_ORRO_REVIEW_PLAN_INVALID,
                "review lane boundary must stay non-executing and non-assurance",
            )
    return payload


def _review_adapter_evidence_root(*, repo: Path, run_dir: Path) -> Path:
    try:
        run_dir.relative_to(repo)
    except ValueError:
        return run_dir

    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    if temp_root == repo or repo in temp_root.parents:
        temp_root = repo.parent
    return Path(
        tempfile.mkdtemp(
            prefix=f"witnessd-orro-review-{run_dir.name}-",
            dir=str(temp_root),
        )
    ).resolve(strict=False)


def _run_review_lane(
    lane: dict[str, Any],
    *,
    repo: Path,
    run_dir: Path,
    evidence_root: Path,
    claude_binary: str,
    agy_binary: str,
    gemini_binary: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    lane_id = str(lane["lane_id"])
    adapter = str(lane["adapter"])
    lane_dir = run_dir / lane_id
    lane_dir.mkdir(parents=True, exist_ok=True)
    adapter_evidence_dir = evidence_root / lane_id
    adapter_evidence_dir.mkdir(parents=True, exist_ok=True)
    model = lane.get("model")
    model_arg = str(model) if isinstance(model, str) and model else None
    try:
        if adapter == "claude":
            result = run_claude_critic_lane(
                sandbox=str(repo),
                prompt=str(lane["prompt"]),
                claude_binary=claude_binary,
                transcript_path=str(adapter_evidence_dir / "events.raw.jsonl"),
                review_receipt_path=str(adapter_evidence_dir / "review-receipt.json"),
                log_path=str(adapter_evidence_dir / "command-log.json"),
                timeout_seconds=timeout_seconds,
                model=model_arg,
                role_id=str(lane["role_id"]),
                lane_id=lane_id,
            )
        elif adapter == "agy":
            result = run_agy_review_lane(
                sandbox=str(repo),
                prompt=str(lane["prompt"]),
                agy_binary=agy_binary,
                transcript_path=str(adapter_evidence_dir / "events.raw.jsonl"),
                review_receipt_path=str(adapter_evidence_dir / "review-receipt.json"),
                log_path=str(adapter_evidence_dir / "command-log.json"),
                timeout_seconds=timeout_seconds,
                model=model_arg,
            )
        elif adapter == "gemini":
            result = run_gemini_review_lane(
                sandbox=str(repo),
                prompt=str(lane["prompt"]),
                gemini_binary=gemini_binary,
                transcript_path=str(adapter_evidence_dir / "events.raw.jsonl"),
                review_receipt_path=str(adapter_evidence_dir / "review-receipt.json"),
                log_path=str(adapter_evidence_dir / "command-log.json"),
                timeout_seconds=timeout_seconds,
            )
            if model_arg is not None:
                result = _with_model_declaration(
                    result,
                    build_model_declaration(
                        adapter="gemini",
                        requested_model=model_arg,
                        verification_status=VERIFICATION_REQUESTED_UNCONFIRMED,
                        detail="gemini review model routing is not live-verified by witnessd",
                    ),
                )
        else:
            raise OrroReviewError(
                ERR_ORRO_REVIEW_LANE_UNSUPPORTED,
                f"unsupported review adapter: {adapter}",
            )
    except (
        AgyAdapterError,
        ClaudeAdapterError,
        GeminiAdapterError,
        AdapterExecutionError,
    ) as exc:
        code = getattr(exc, "code", ERR_ORRO_REVIEW_LANE_UNSUPPORTED)
        message = getattr(exc, "message", str(exc))
        raise OrroReviewError(code, message) from exc

    model_declaration_path = None
    if result.model_declaration is not None:
        model_declaration_path = lane_dir / "model-declaration.json"
        _write_json(model_declaration_path, result.model_declaration)
    review_receipt = _load_json_object(Path(str(result.review_receipt_path)))
    return {
        "lane_id": lane_id,
        "role_id": lane["role_id"],
        "phase": lane["phase"],
        "adapter": adapter,
        "model": model_arg,
        "region": lane["region"],
        "exit_code": result.exit_code,
        "touched_files": result.touched_files,
        "test_output": result.test_output,
        "transcript_path": result.transcript_path,
        "normalized_events_path": result.normalized_events_path,
        "review_receipt_path": result.review_receipt_path,
        "review_receipt": review_receipt,
        "model_declaration_path": (
            str(model_declaration_path) if model_declaration_path is not None else None
        ),
        "model_declaration": result.model_declaration,
        "can_change_evidence_verdict": False,
        "raises_assurance": False,
        "verifies_evidence": False,
    }


def _with_model_declaration(
    result: AdapterResult, declaration: dict[str, Any]
) -> AdapterResult:
    return AdapterResult(
        adapter=result.adapter,
        runner_kind=result.runner_kind,
        invocation=result.invocation,
        exit_code=result.exit_code,
        transcript_path=result.transcript_path,
        command_receipts=result.command_receipts,
        touched_files=result.touched_files,
        test_output=result.test_output,
        normalized_events=result.normalized_events,
        raw_events_path=result.raw_events_path,
        normalized_events_path=result.normalized_events_path,
        review_receipt_path=result.review_receipt_path,
        model_declaration=declaration,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrroReviewError(ERR_ORRO_REVIEW_PLAN_LOAD_FAILED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OrroReviewError(ERR_ORRO_REVIEW_PLAN_INVALID, f"{path} is not an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OrroReviewError(ERR_ORRO_REVIEW_WRITE_FAILED, str(exc)) from exc
