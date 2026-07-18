from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.cli._output import _emit_orro_error, _run_depone_json
from witnessd.__main__ import main

def _cmd_orro_next(args: argparse.Namespace) -> int:
    from witnessd.orro_next import OrroNextError, decide_next, write_decision

    if not args.run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_NEXT_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    run_dir = Path(args.run_dir).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    code, payload = decide_next(run_dir, home=home)
    if args.out:
        try:
            write_decision(Path(args.out).resolve(strict=False), payload)
        except OrroNextError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    print(json.dumps(payload, sort_keys=True))
    return code


def _cmd_orro_advise(args: argparse.Namespace) -> int:
    from witnessd.orro_workstyle import (
        OrroWorkstyleError,
        advise_workstyle,
        write_workstyle_decision,
    )

    if not args.goal or not str(args.goal).strip():
        _emit_orro_error(
            args,
            code="ERR_ORRO_ADVISE_INPUT_REQUIRED",
            message="goal is required",
        )
        return 2
    repo = Path(args.repo).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    payload = advise_workstyle(str(args.goal), repo=repo, home=home)
    if args.out:
        try:
            write_workstyle_decision(Path(args.out).resolve(strict=False), payload)
        except OrroWorkstyleError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


def _cmd_orro_sketch(args: argparse.Namespace) -> int:
    from witnessd.advisory_provenance import emit_advisory_provenance
    from witnessd.orro_advisory import (
        OrroAdvisoryError,
        build_sketch_decision,
        read_agent_decision,
        write_advisory_decision,
    )
    from witnessd.signing import DsseSigningError

    if not args.goal or not str(args.goal).strip():
        _emit_orro_error(
            args,
            code="ERR_ORRO_SKETCH_INPUT_REQUIRED",
            message="goal is required",
        )
        return 2
    repo = Path(args.repo).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    try:
        decision = read_agent_decision(Path(args.decision)) if args.decision else None
        payload = build_sketch_decision(
            str(args.goal), repo=repo, home=home, decision=decision
        )
        if args.out:
            out_path = Path(args.out).resolve(strict=False)
            write_advisory_decision(out_path, payload)
            seal_home = home or (out_path.parent.parent / ".witnessd")
            payload = emit_advisory_provenance(
                payload,
                decision_path=out_path,
                home=seal_home,
                repo=repo,
            )
    except OrroAdvisoryError as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 1
    except (DsseSigningError, OSError) as exc:
        _emit_orro_error(
            args,
            code=getattr(exc, "code", "ERR_ORRO_ADVISORY_WRITE_FAILED"),
            message=str(exc),
        )
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


def _cmd_orro_trace(args: argparse.Namespace) -> int:
    from witnessd.advisory_provenance import emit_advisory_provenance
    from witnessd.orro_advisory import (
        OrroAdvisoryError,
        build_trace_decision,
        read_agent_decision,
        write_advisory_decision,
    )
    from witnessd.signing import DsseSigningError

    if not args.goal or not str(args.goal).strip():
        _emit_orro_error(
            args,
            code="ERR_ORRO_TRACE_INPUT_REQUIRED",
            message="goal or symptom is required",
        )
        return 2
    repo = Path(args.repo).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    try:
        decision = read_agent_decision(Path(args.decision)) if args.decision else None
        payload = build_trace_decision(
            str(args.goal), repo=repo, home=home, decision=decision
        )
        if args.out:
            out_path = Path(args.out).resolve(strict=False)
            write_advisory_decision(out_path, payload)
            seal_home = home or (out_path.parent.parent / ".witnessd")
            payload = emit_advisory_provenance(
                payload,
                decision_path=out_path,
                home=seal_home,
                repo=repo,
            )
    except OrroAdvisoryError as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 1
    except (DsseSigningError, OSError) as exc:
        _emit_orro_error(
            args,
            code=getattr(exc, "code", "ERR_ORRO_ADVISORY_WRITE_FAILED"),
            message=str(exc),
        )
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


def _cmd_orro_report(args: argparse.Namespace) -> int:
    from witnessd.orro_report import (
        OrroReportError,
        build_report,
        render_text_report,
        write_report,
    )

    if not args.run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_REPORT_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    run_dir = Path(args.run_dir).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    workstyle = (
        Path(args.workstyle_decision).resolve(strict=False)
        if args.workstyle_decision
        else None
    )
    try:
        code, payload = build_report(run_dir, home=home, workstyle_decision=workstyle)
        if args.out:
            write_report(Path(args.out).resolve(strict=False), payload)
    except OrroReportError as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 1
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(render_text_report(payload), end="")
    return code


def _cmd_orro_review(args: argparse.Namespace) -> int:
    from witnessd.orro_review import OrroReviewError, run_review_role_lane_plan

    if not args.role_lane_plan:
        _emit_orro_error(
            args,
            code="ERR_ORRO_REVIEW_ROLE_LANE_PLAN_REQUIRED",
            message="--role-lane-plan is required",
        )
        return 2
    repo = Path(args.repo).resolve(strict=False)
    home = Path(
        args.home or os.environ.get("WITNESSD_HOME") or (repo / ".witnessd")
    ).resolve(strict=False)
    run_dir = Path(args.run_dir).resolve(strict=False) if args.run_dir else None
    try:
        code, payload = run_review_role_lane_plan(
            repo=repo,
            home=home,
            role_lane_plan_path=Path(args.role_lane_plan).resolve(strict=False),
            run_dir=run_dir,
            claude_binary=args.claude_binary,
            agy_binary=args.agy_binary,
            gemini_binary=args.gemini_binary,
            timeout_seconds=args.timeout_seconds,
        )
    except OrroReviewError as exc:
        _emit_orro_error(args, code=exc.code, message=exc.message)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return code


def _cmd_orro_auto(args: argparse.Namespace) -> int:
    from witnessd.orro_auto import (
        OrroAutoError,
        build_auto_plan,
        build_auto_receipt,
        build_auto_session,
        write_auto_plan,
        write_auto_receipt,
        write_auto_session,
    )

    mode_count = sum(
        bool(mode) for mode in (args.dry_run, args.once, args.until_complete)
    )
    if mode_count > 1:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_MODE_CONFLICT",
            message="choose exactly one of --dry-run, --once, or --until-complete",
        )
        return 2
    if mode_count == 0:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_DRY_RUN_REQUIRED",
            message="orro auto requires --dry-run, --once, or --until-complete",
        )
        return 2
    if args.until_complete and args.max_steps is None:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_MAX_STEPS_REQUIRED",
            message="orro auto --until-complete requires --max-steps",
        )
        return 2
    if args.until_complete and args.max_steps not in {1, 2}:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_MAX_STEPS_INVALID",
            message="orro auto --until-complete supports --max-steps 1 or 2 in v0",
        )
        return 2
    if not args.run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    run_dir = Path(args.run_dir).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    code, payload = build_auto_plan(run_dir, home=home)
    if args.dry_run and args.out:
        try:
            write_auto_plan(Path(args.out).resolve(strict=False), payload)
        except OrroAutoError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    if args.dry_run:
        print(json.dumps(payload, sort_keys=True))
        return code

    if args.until_complete:
        max_steps = int(args.max_steps)
        decision_initial = str(payload.get("decision", "blocked"))
        current_code = code
        current_payload = payload
        steps: list[dict[str, object]] = []
        error = None
        reasons: list[str] = []

        while len(steps) < max_steps:
            decision = str(current_payload.get("decision", "blocked"))
            if decision == "complete":
                break
            would_run = current_payload.get("would_run", [])
            if current_code != 0 or not would_run:
                payload_reasons = current_payload.get("reasons", [])
                reasons = (
                    list(payload_reasons) if isinstance(payload_reasons, list) else []
                )
                maybe_error = current_payload.get("error")
                error = maybe_error if isinstance(maybe_error, dict) else None
                break
            child_code, receipt, after_code, after_payload = _run_orro_auto_step(
                run_dir,
                home=home,
            )
            steps.append(
                {
                    "step_index": len(steps) + 1,
                    "decision_before": receipt["decision_before"],
                    "executed_phase": receipt["executed_phase"],
                    "command": receipt["command"],
                    "exit_code": receipt["exit_code"],
                    "decision_after": receipt["decision_after"],
                    "wrote": receipt["wrote"],
                    "launches_workers": False,
                    "executes_proofrun": False,
                    "raises_assurance": False,
                }
            )
            current_code = after_code
            current_payload = after_payload
            if child_code != 0:
                maybe_error = receipt.get("error")
                error = maybe_error if isinstance(maybe_error, dict) else None
                break

        decision_final = str(current_payload.get("decision", "blocked"))
        complete = decision_final == "complete"
        blocked = not complete
        if blocked and error is None:
            if len(steps) >= max_steps and decision_final in {
                "needs-proofcheck",
                "ready-for-handoff",
            }:
                error = {
                    "code": "ERR_ORRO_AUTO_MAX_STEPS_REACHED",
                    "message": "orro auto --until-complete stopped before complete because --max-steps was reached",
                }
                reasons = [*reasons, "max steps reached before completion"]
            else:
                maybe_error = current_payload.get("error")
                error = (
                    maybe_error
                    if isinstance(maybe_error, dict)
                    else {
                        "code": "ERR_ORRO_AUTO_BLOCKED",
                        "message": "ORRO auto until-complete is blocked by continuation state",
                    }
                )
                payload_reasons = current_payload.get("reasons", reasons)
                reasons = (
                    list(payload_reasons)
                    if isinstance(payload_reasons, list)
                    else reasons
                )
        session = build_auto_session(
            run_dir,
            max_steps=max_steps,
            steps=steps,
            decision_initial=decision_initial,
            decision_final=decision_final,
            complete=complete,
            blocked=blocked,
            reasons=reasons,
            error=error,
        )
        if args.out:
            try:
                write_auto_session(Path(args.out).resolve(strict=False), session)
            except OrroAutoError as exc:
                _emit_orro_error(args, code=exc.code, message=str(exc))
                return 1
        print(json.dumps(session, sort_keys=True))
        if complete:
            return 0
        if decision_final == "invalid-run-dir":
            return 2
        return 1

    decision_before = str(payload.get("decision", "blocked"))
    would_run = payload.get("would_run", [])
    if not would_run:
        receipt = build_auto_receipt(
            run_dir,
            decision_before=decision_before,
            executed=False,
            executed_phase=None,
            command=[],
            exit_code=0 if decision_before == "complete" else code,
            decision_after=decision_before,
            wrote=[],
            reasons=list(payload.get("reasons", [])),
            error=payload.get("error")
            if isinstance(payload.get("error"), dict)
            else None,
        )
        if args.out:
            try:
                write_auto_receipt(Path(args.out).resolve(strict=False), receipt)
            except OrroAutoError as exc:
                _emit_orro_error(args, code=exc.code, message=str(exc))
                return 1
        print(json.dumps(receipt, sort_keys=True))
        if decision_before == "complete":
            return 0
        return code

    child_code, receipt, _after_code, _after_payload = _run_orro_auto_step(
        run_dir,
        home=home,
    )
    if args.out:
        try:
            write_auto_receipt(Path(args.out).resolve(strict=False), receipt)
        except OrroAutoError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    print(json.dumps(receipt, sort_keys=True))
    return child_code


def _run_orro_auto_step(
    run_dir: Path,
    *,
    home: Path | None,
) -> tuple[int, dict[str, object], int, dict[str, object]]:
    from witnessd.orro_auto import build_auto_plan, build_auto_receipt

    before_code, before_payload = build_auto_plan(run_dir, home=home)
    decision_before = str(before_payload.get("decision", "blocked"))
    would_run = before_payload.get("would_run", [])
    step = would_run[0] if isinstance(would_run, list) and would_run else None
    command = list(step.get("command", [])) if isinstance(step, dict) else []
    phase = str(step.get("phase", "")) if isinstance(step, dict) else ""
    if phase not in {"proofcheck", "handoff"} or not command:
        receipt = build_auto_receipt(
            run_dir,
            decision_before=decision_before,
            executed=False,
            executed_phase=None,
            command=[],
            exit_code=1,
            decision_after=decision_before,
            wrote=[],
            reasons=["unsupported auto continuation decision"],
            error={
                "code": "ERR_ORRO_AUTO_UNSUPPORTED_DECISION",
                "message": "orro auto execution only supports proofcheck and handoff",
            },
        )
        return 1, receipt, before_code, before_payload

    child_stdout = io.StringIO()
    with redirect_stdout(child_stdout):
        child_code = main(command)
    after_code, after_payload = build_auto_plan(run_dir, home=home)
    decision_after = str(after_payload.get("decision", "blocked"))
    wrote = []
    if phase == "proofcheck" and (run_dir / "proofcheck-verdict.json").is_file():
        wrote.append("proofcheck-verdict.json")
    if phase == "handoff" and (run_dir / "orro-handoff.json").is_file():
        wrote.append("orro-handoff.json")
    error = None
    if child_code != 0:
        error = {
            "code": "ERR_ORRO_AUTO_BLOCKED",
            "message": child_stdout.getvalue(),
        }
    receipt = build_auto_receipt(
        run_dir,
        decision_before=decision_before,
        executed=True,
        executed_phase=phase,
        command=command,
        exit_code=child_code,
        decision_after=decision_after,
        wrote=wrote,
        error=error,
    )
    return (
        child_code,
        receipt,
        after_code if before_code == 0 or child_code == 0 else before_code,
        after_payload,
    )
