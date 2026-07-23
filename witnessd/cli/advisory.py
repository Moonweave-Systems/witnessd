from __future__ import annotations

import argparse
import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.cli._output import (
    _emit_orro_error as _base_emit_orro_error,
    _run_depone_json,  # noqa: F401 - preserved report no-execution patch seam
    _with_structured_error,
    _write_json_file,
)
from witnessd.__main__ import main


def _select_latest_run(args: argparse.Namespace, *, command: str) -> tuple[Path | None, Path | None]:
    from witnessd.cli.status import latest_run_dir, resolve_home

    if args.latest and args.run_dir:
        _base_emit_orro_error(
            args,
            code=f"ERR_ORRO_{command.upper()}_RUN_DIR_CONFLICT",
            message=f"orro {command} --latest cannot be combined with a run directory",
            reason="choose one run directory source",
            required_input_or_grant="either --latest or <run-dir>, not both",
            next_command=f"python3 -m orro {command} --latest --home <home> --json",
        )
        return None, None
    if args.latest:
        home = resolve_home(args.home, Path.cwd())
        run_dir = latest_run_dir(home)
        if run_dir is None:
            _base_emit_orro_error(
                args,
                code=f"ERR_ORRO_{command.upper()}_LATEST_NO_RUNS",
                message=f"no ORRO runs found under {home / 'runs'}",
                reason=f"the latest run lookup searched {home / 'runs'}",
                required_input_or_grant="a run directory under <home>/runs",
                next_command=f"python3 -m orro {command} <run-dir> --home {home} --json",
            )
            return None, home
        return run_dir, home
    return (Path(args.run_dir).resolve(strict=False) if args.run_dir else None), None


ADVISORY_REMEDIATION = {
    "orro-next": (
        "continuation needs a readable run directory with valid persisted ORRO artifacts",
        "an existing ORRO run directory",
        "python3 -m orro next <run-dir> --home .witnessd --json",
    ),
    "orro-advise": (
        "workstyle advice needs a concrete goal and a writable output path when requested",
        "a non-empty goal and an accessible repository",
        "python3 -m orro advise \"<goal>\" --repo <repo> --home .witnessd --json",
    ),
    "orro-sketch": (
        "sketch needs a concrete goal and valid advisory inputs before it can seal a direction",
        "a non-empty goal, accessible repository, and valid decision file when supplied",
        "python3 -m orro sketch \"<goal>\" --repo <repo> --home .witnessd --json",
    ),
    "orro-trace": (
        "trace needs a concrete symptom and valid advisory inputs before it can seal a root-cause record",
        "a non-empty symptom, accessible repository, and valid decision file when supplied",
        "python3 -m orro trace \"<symptom>\" --repo <repo> --home .witnessd --json",
    ),
    "orro-report": (
        "report needs a readable run directory with internally consistent persisted artifacts",
        "an existing ORRO run directory",
        "python3 -m orro report <run-dir> --home .witnessd --json",
    ),
    "orro-review": (
        "review needs a valid review-only role-lane plan and available read-only adapter",
        "an accessible repository and a valid --role-lane-plan",
        "python3 -m orro review --repo <repo> --home .witnessd --role-lane-plan <plan.json> --json",
    ),
    "orro-auto": (
        "auto requires one explicit mode; run-item executes declared steps only behind evidence gates",
        "a valid run directory, or --run-item with --repo and --max-steps",
        "python3 -m orro auto --run-item <item> --repo <repo> --home .witnessd --max-steps 1 --json",
    ),
}


def _advisory_remediation(args: argparse.Namespace) -> tuple[str, str, str]:
    return ADVISORY_REMEDIATION.get(
        str(getattr(args, "cmd", "")),
        (
            "the advisory command is blocked by missing or invalid input",
            "valid advisory command input",
            "python3 -m orro --help",
        ),
    )


def _emit_orro_error(
    args: argparse.Namespace, *, code: str, message: str
) -> None:
    reason, required_input_or_grant, next_command = _advisory_remediation(args)
    _base_emit_orro_error(
        args,
        code=code,
        message=message,
        reason=reason,
        required_input_or_grant=required_input_or_grant,
        next_command=next_command,
    )


def _with_advisory_error(
    args: argparse.Namespace,
    payload: dict[str, object],
    *,
    default_code: str,
    default_message: str,
) -> dict[str, object]:
    reason, required_input_or_grant, next_command = _advisory_remediation(args)
    return _with_structured_error(
        payload,
        default_code=default_code,
        default_message=default_message,
        reason=reason,
        required_input_or_grant=required_input_or_grant,
        next_command=next_command,
    )


def _emit_deprecation(args: argparse.Namespace, replacement: str) -> None:
    alias = getattr(args, "_deprecated_alias", None)
    if alias:
        mapping = f" (alias: {alias})" if alias == "next" else ""
        print(
            f"deprecated: use orro {replacement} (this alias will be removed in a future release){mapping}",
            file=os.sys.stderr,
        )


def _auto_advisory_mode(goal: str, *, task_class: str) -> str:
    if re.search(
        r"\b(?:broken|break|crash|error|exception|fail(?:ure|ing)?|regression|symptom|traceback)\b",
        goal.lower(),
    ):
        return "trace"
    if task_class == "code-change" and re.search(
        r"\b(?:add|build|create|implement|introduce|new|feature)\b", goal.lower()
    ):
        return "sketch"
    return "route"

def _cmd_orro_next(args: argparse.Namespace) -> int:
    from witnessd.orro_next import OrroNextError, decide_next, write_decision

    run_dir, latest_home = _select_latest_run(args, command="next")
    if args.latest and run_dir is None:
        return 2
    if not run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_NEXT_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    home = latest_home or (Path(args.home).resolve(strict=False) if args.home else None)
    code, payload = decide_next(run_dir, home=home)
    if args.out:
        try:
            write_decision(Path(args.out).resolve(strict=False), payload)
        except OrroNextError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    if code != 0:
        payload = _with_advisory_error(
            args,
            payload,
            default_code="ERR_ORRO_NEXT_BLOCKED",
            default_message="ORRO continuation is blocked",
        )
    if getattr(args, "_deprecated_alias", None) == "next":
        print(
            "deprecated: use orro auto --dry-run (this alias will be removed in a future release)",
            file=os.sys.stderr,
        )
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
    mode = args.mode
    if mode == "auto":
        routed = advise_workstyle(
            str(args.goal),
            repo=Path(args.repo).resolve(strict=False),
            home=Path(args.home).resolve(strict=False) if args.home else None,
        )
        mode = _auto_advisory_mode(
            str(args.goal), task_class=str(routed["task_class"])
        )
    if mode == "sketch":
        return _cmd_orro_sketch(args)
    if mode == "trace":
        return _cmd_orro_trace(args)
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
    from witnessd.orro_intent import declared_intent_ref, read_declared_intent
    from witnessd.signing import DsseSigningError

    _emit_deprecation(args, "advise --mode sketch")

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
        declared_intent = (
            read_declared_intent(Path(args.intent)) if args.intent else None
        )
        intent_reference = None
        if args.intent:
            intent_path = Path(args.intent).resolve(strict=False)
            if args.out:
                intent_path = (
                    Path(args.out).resolve(strict=False).parent
                    / "declared-intent.json"
                )
                assert declared_intent is not None
                _write_json_file(intent_path, declared_intent)
            intent_reference = declared_intent_ref(intent_path)
        payload = build_sketch_decision(
            str(args.goal),
            repo=repo,
            home=home,
            decision=decision,
            declared_intent=declared_intent,
            declared_intent_reference=intent_reference,
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

    _emit_deprecation(args, "advise --mode trace")

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
    from witnessd.orro_advisory import OrroAdvisoryError
    from witnessd.orro_intent import read_declared_intent
    from witnessd.orro_report import (
        OrroReportError,
        build_report,
        render_text_report,
        write_report,
    )

    run_dir, latest_home = _select_latest_run(args, command="report")
    if args.latest and run_dir is None:
        return 2
    if not run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_REPORT_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    home = latest_home or (Path(args.home).resolve(strict=False) if args.home else None)
    workstyle = (
        Path(args.workstyle_decision).resolve(strict=False)
        if args.workstyle_decision
        else None
    )
    try:
        declared_intent = (
            read_declared_intent(Path(args.intent)) if args.intent else None
        )
        intent_source = (
            Path(args.intent).resolve(strict=False) if args.intent else None
        )
        code, payload = build_report(
            run_dir,
            home=home,
            workstyle_decision=workstyle,
            declared_intent=declared_intent,
            declared_intent_source=intent_source,
        )
        if args.out:
            write_report(Path(args.out).resolve(strict=False), payload)
    except (OrroAdvisoryError, OrroReportError) as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 1
    if code != 0:
        payload = _with_advisory_error(
            args,
            payload,
            default_code="ERR_ORRO_REPORT_BLOCKED",
            default_message="ORRO report cannot summarize this run as continuable",
        )
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
    if code != 0:
        payload = _with_advisory_error(
            args,
            payload,
            default_code="ERR_ORRO_REVIEW_BLOCKED",
            default_message="ORRO review did not complete",
        )
    print(json.dumps(payload, sort_keys=True))
    return code


def _cmd_orro_auto(args: argparse.Namespace) -> int:
    from witnessd.orro_auto import (
        OrroAutoError,
        build_auto_plan,
        build_auto_receipt,
        build_auto_session,
        run_item_session,
        write_auto_plan,
        write_auto_receipt,
        write_auto_session,
    )

    run_item = args.run_item is not None
    mode_count = sum(bool(mode) for mode in (args.dry_run, args.once, args.until_complete, run_item))
    if mode_count > 1:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_MODE_CONFLICT",
            message="choose exactly one of --dry-run, --once, --until-complete, or --run-item",
        )
        return 2
    if args.latest and args.run_dir:
        _base_emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_RUN_DIR_CONFLICT",
            message="orro auto --latest cannot be combined with a run directory",
            reason="choose one run directory source",
            required_input_or_grant="either --latest or <run-dir>, not both",
            next_command="python3 -m orro auto --latest --dry-run --home <home> --json",
        )
        return 2
    if run_item:
        if args.run_dir:
            _emit_orro_error(
                args,
                code="ERR_ORRO_AUTO_RUN_DIR_CONFLICT",
                message="orro auto --run-item cannot be combined with a run directory",
            )
            return 2
        if args.max_steps is None:
            _emit_orro_error(
                args,
                code="ERR_ORRO_AUTO_MAX_STEPS_REQUIRED",
                message="orro auto --run-item requires --max-steps",
            )
            return 2
        if args.max_steps < 1:
            _emit_orro_error(
                args,
                code="ERR_ORRO_AUTO_MAX_STEPS_INVALID",
                message="orro auto --run-item requires a positive --max-steps",
            )
            return 2
        if not args.repo:
            _emit_orro_error(
                args,
                code="ERR_ORRO_AUTO_REPO_REQUIRED",
                message="orro auto --run-item requires --repo",
            )
            return 2
        repo = Path(args.repo).resolve(strict=False)
        home = Path(args.home).resolve(strict=False) if args.home else repo / ".witnessd"
        code, session = run_item_session(
            repo=repo,
            home=home,
            item_id=str(args.run_item),
            max_steps=int(args.max_steps),
        )
        receipt_path = Path(args.out).resolve(strict=False) if args.out else home / "orro-auto-session.json"
        try:
            write_auto_session(receipt_path, session)
        except OrroAutoError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
        if code != 0:
            session = _with_advisory_error(
                args,
                session,
                default_code="ERR_ORRO_AUTO_BLOCKED",
                default_message="ORRO auto run-item stopped before completion",
            )
        print(json.dumps(session, sort_keys=True))
        return code
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
    run_dir, latest_home = _select_latest_run(args, command="auto")
    if args.latest and run_dir is None:
        return 2
    if not run_dir:
        _emit_orro_error(
            args,
            code="ERR_ORRO_AUTO_INPUT_REQUIRED",
            message="run directory is required",
        )
        return 2
    home = latest_home or (Path(args.home).resolve(strict=False) if args.home else None)
    code, payload = build_auto_plan(run_dir, home=home)
    if args.dry_run and args.out:
        try:
            write_auto_plan(Path(args.out).resolve(strict=False), payload)
        except OrroAutoError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    if args.dry_run:
        if code != 0:
            payload = _with_advisory_error(
                args,
                payload,
                default_code="ERR_ORRO_AUTO_BLOCKED",
                default_message="ORRO auto planning is blocked",
            )
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
        if not complete:
            session = _with_advisory_error(
                args,
                session,
                default_code="ERR_ORRO_AUTO_BLOCKED",
                default_message="ORRO auto did not reach complete state",
            )
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
        if decision_before != "complete":
            receipt = _with_advisory_error(
                args,
                receipt,
                default_code="ERR_ORRO_AUTO_BLOCKED",
                default_message="ORRO auto cannot execute from the current continuation state",
            )
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
    if child_code != 0:
        receipt = _with_advisory_error(
            args,
            receipt,
            default_code="ERR_ORRO_AUTO_BLOCKED",
            default_message="ORRO auto step did not complete",
        )
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
