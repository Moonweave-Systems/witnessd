from __future__ import annotations

import argparse
import json
import os
import shlex
import tempfile
import time
from pathlib import Path

from witnessd.cli._output import (
    _invoke_cli_capture,
    _json_or_text,
    _structured_error,
)
from witnessd.cli.team_ops import _paths_overlap


def _cmd_orro_flow(args: argparse.Namespace) -> int:
    try:
        return _run_orro_flow(args)
    except Exception as exc:  # noqa: BLE001 - guided flow never leaks tracebacks
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="init",
            run_dir=None,
            phases=[],
            error=_structured_error(
                code="ERR_ORRO_FLOW_UNEXPECTED_BLOCKER",
                message=str(exc) or exc.__class__.__name__,
                reason="the guided orchestration boundary caught an unexpected error",
                required_input_or_grant="resolve the reported local readiness error",
                next_command=(
                    "python3 -m witnessd init "
                    f"--home {shlex.quote(str(args.home or '.witnessd'))}"
                ),
            ),
        )


def _run_orro_flow(args: argparse.Namespace) -> int:
    repo = (
        Path(args.repo).resolve(strict=False)
        if args.repo
        else Path.cwd().resolve(strict=False)
    )
    home = Path(
        args.home or os.environ.get("WITNESSD_HOME") or (repo / ".witnessd")
    ).resolve(strict=False)
    run_dir = (
        Path(args.run_dir).resolve(strict=False)
        if args.run_dir
        else home
        / "runs"
        / f"flow-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
    )
    phases: list[dict[str, object]] = []

    if not args.write_scope:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="flowplan",
            run_dir=None,
            phases=phases,
            error=_structured_error(
                code="ERR_ORRO_FLOW_WRITE_SCOPE_REQUIRED",
                message="orro flow requires at least one --write-scope glob",
                reason=(
                    "write_scope is the user-controlled safety boundary and cannot "
                    "be inferred or widened"
                ),
                required_input_or_grant="--write-scope '<glob>' (repeatable)",
                next_command=(
                    "python3 -m orro flow "
                    f"{shlex.quote(str(args.goal))} --write-scope '<glob>' "
                    f"--adapter {shlex.quote(str(args.adapter))} --json"
                    + (" --verification-only" if args.verification_only else "")
                ),
            ),
        )
    if not args.adapter:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="flowplan",
            run_dir=None,
            phases=phases,
            error=_structured_error(
                code="ERR_ORRO_FLOW_ADAPTER_REQUIRED",
                message="orro flow requires --adapter",
                reason="the executing adapter must be chosen explicitly",
                required_input_or_grant=(
                    "--adapter codex|claude|agy|gemini|opencode"
                ),
                next_command=(
                    "python3 -m orro flow "
                    f"{shlex.quote(str(args.goal))} "
                    + " ".join(
                        f"--write-scope {shlex.quote(scope)}"
                        for scope in args.write_scope
                    )
                    + " --adapter <adapter> --json"
                    + (" --verification-only" if args.verification_only else "")
                ),
            ),
        )

    runner_sandbox = (
        Path(args.runner_sandbox).resolve(strict=False)
        if args.runner_sandbox
        else Path(tempfile.mkdtemp(prefix="orro-flow-runner-")).resolve(strict=False)
    )
    from witnessd.cli.team_ops import _paths_overlap

    if _paths_overlap(runner_sandbox, run_dir):
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="proofrun",
            run_dir=run_dir,
            phases=phases,
            error=_structured_error(
                code="ERR_ORRO_FLOW_RUNNER_NOT_SEPARATED",
                message="runner sandbox overlaps the observer run directory",
                reason=(
                    "proofrun must preserve observer/runner filesystem separation"
                ),
                required_input_or_grant=(
                    "--runner-sandbox <dir> outside the --run-dir tree"
                ),
                next_command=(
                    "python3 -m witnessd proofrun "
                    f"{shlex.quote(str(args.goal))} --repo {shlex.quote(str(repo))} "
                    f"--home {shlex.quote(str(home))} --runner-sandbox <dir> "
                    "--workflow-plan <workflow-plan.json> "
                    "--role-lane-plan <role-lane-plan.json> --json"
                ),
            ),
        )

    run_dir.mkdir(parents=True, exist_ok=True)
    runner_sandbox.mkdir(parents=True, exist_ok=True)
    scout_dir = run_dir / "scout"
    workflow_plan_path = run_dir / "workflow-plan.json"
    role_lane_plan_path = run_dir / "role-lane-plan.json"
    proofcheck_path = run_dir / "proofcheck-verdict.json"
    rolepack_path = (
        Path(args.rolepack_file).resolve(strict=False)
        if args.rolepack_file
        else run_dir / "generated-rolepack.json"
    )

    init_argv = ["init", "--home", str(home), "--repo", str(repo)]
    init_code, init_payload, init_error = _invoke_orro_flow_phase(init_argv)
    if init_code != 0:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="init",
            run_dir=run_dir,
            phases=phases,
            error=_orro_flow_phase_error(
                phase="init",
                argv=init_argv,
                payload=init_payload,
                fallback_message=init_error,
            ),
        )
    phases.append(
        {
            "phase": "init",
            "status": "ok",
            "artifact": str(home / "provision.json"),
        }
    )

    scout_argv = [
        "scout",
        str(args.goal),
        "--repo",
        str(repo),
        "--home",
        str(home),
        "--out-dir",
        str(scout_dir),
    ]
    scout_code, scout_payload, scout_error = _invoke_orro_flow_phase(scout_argv)
    if scout_code != 0:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="scout",
            run_dir=run_dir,
            phases=phases,
            error=_orro_flow_phase_error(
                phase="scout",
                argv=scout_argv,
                payload=scout_payload,
                fallback_message=scout_error,
            ),
        )
    phases.append({"phase": "scout", "status": "ok", "artifact": str(scout_dir)})

    if args.rolepack_file:
        from witnessd.role_capability import RolepackError, load_rolepack_file

        try:
            supplied_rolepack = load_rolepack_file(str(rolepack_path))
        except (OSError, RolepackError, ValueError) as exc:
            return _emit_orro_flow_blocker(
                args,
                blocked_phase="flowplan",
                run_dir=run_dir,
                phases=phases,
                error=_structured_error(
                    code=str(
                        getattr(exc, "code", "ERR_ORRO_FLOW_ROLEPACK_INVALID")
                    ),
                    message=str(exc),
                    reason="the supplied rolepack could not be loaded and validated",
                    required_input_or_grant="--rolepack-file <valid-rolepack.json>",
                    next_command=(
                        "python3 -m witnessd team init "
                        f"--role runner:{shlex.quote(str(args.adapter))} "
                        + " ".join(
                            f"--write-scope {shlex.quote(scope)}"
                            for scope in args.write_scope
                        )
                        + f" --out {shlex.quote(str(rolepack_path))} --yes"
                    ),
                ),
            )
        execute_scopes = {
            str(scope)
            for grant in supplied_rolepack.get("grants", [])
            if isinstance(grant, dict) and grant.get("capability") == "execute"
            for scope in grant.get("write_scope", [])
            if isinstance(scope, str)
        }
        requested_scopes = set(args.write_scope)
        if execute_scopes != requested_scopes:
            return _emit_orro_flow_blocker(
                args,
                blocked_phase="flowplan",
                run_dir=run_dir,
                phases=phases,
                error=_structured_error(
                    code="ERR_ORRO_FLOW_WRITE_SCOPE_MISMATCH",
                    message=(
                        "supplied rolepack write_scope differs from --write-scope"
                    ),
                    reason=(
                        "rolepack execute write scopes must exactly match the "
                        "user-provided flow safety boundary"
                    ),
                    required_input_or_grant=(
                        "a rolepack whose execute grants use exactly: "
                        + ", ".join(sorted(requested_scopes))
                    ),
                    next_command=(
                        "python3 -m witnessd team init "
                        f"--role runner:{shlex.quote(str(args.adapter))} "
                        + " ".join(
                            f"--write-scope {shlex.quote(scope)}"
                            for scope in args.write_scope
                        )
                        + f" --out {shlex.quote(str(rolepack_path))} --yes"
                    ),
                ),
            )

    if not args.rolepack_file:
        from witnessd.orro_team_surface import (
            OrroTeamSurfaceError,
            build_rolepack_scaffold,
            write_rolepack_scaffold,
        )

        try:
            generated_rolepack = build_rolepack_scaffold(
                template=None,
                roles=[f"runner:{args.adapter}"],
                write_scope=list(args.write_scope),
            )
            write_rolepack_scaffold(rolepack_path, generated_rolepack, yes=True)
        except (OrroTeamSurfaceError, OSError, ValueError) as exc:
            code = getattr(exc, "code", "ERR_ORRO_FLOW_ROLEPACK_BUILD_FAILED")
            return _emit_orro_flow_blocker(
                args,
                blocked_phase="flowplan",
                run_dir=run_dir,
                phases=phases,
                error=_structured_error(
                    code=str(code),
                    message=str(exc),
                    reason="the generated rolepack could not be validated or written",
                    required_input_or_grant="--rolepack-file <rolepack.json>",
                    next_command=(
                        "python3 -m witnessd team init "
                        f"--role runner:{shlex.quote(str(args.adapter))} "
                        + " ".join(
                            f"--write-scope {shlex.quote(scope)}"
                            for scope in args.write_scope
                        )
                        + f" --out {shlex.quote(str(rolepack_path))} --yes"
                    ),
                ),
            )

    from witnessd.orro_workstyle import advise_workstyle

    try:
        advice = advise_workstyle(str(args.goal), repo=repo, home=home)
    except Exception as exc:  # noqa: BLE001 - advisory failure is a flowplan blocker
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="flowplan",
            run_dir=run_dir,
            phases=phases,
            error=_structured_error(
                code="ERR_ORRO_FLOW_WORKSTYLE_BLOCKED",
                message=str(exc) or exc.__class__.__name__,
                reason="the existing workstyle gate could not classify the goal",
                required_input_or_grant="a goal that the workstyle gate can classify",
                next_command=(
                    "python3 -m witnessd orro-advise "
                    f"{shlex.quote(str(args.goal))} --repo {shlex.quote(str(repo))} "
                    f"--home {shlex.quote(str(home))} --json"
                ),
            ),
        )
    if advice.get("task_class") == "risky-change":
        flowplan_next = _orro_flow_flowplan_command(
            goal=str(args.goal),
            repo=repo,
            workflow_plan_path=workflow_plan_path,
            role_lane_plan_path=role_lane_plan_path,
            rolepack_path=rolepack_path,
            adapter=str(args.adapter),
            tier=str(args.role_lane_tier),
            verification_only=args.verification_only,
        )
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="flowplan",
            run_dir=run_dir,
            phases=phases,
            error=_structured_error(
                code="ERR_ORRO_FLOW_RISKY_CHANGE_REVIEW_REQUIRED",
                message="risky-change goal requires human review before execution",
                reason="risky changes require human review and explicit execution gates",
                required_input_or_grant=(
                    "a human-reviewed rolepack and explicit manual flowplan review"
                ),
                next_command=flowplan_next,
            ),
        )

    flowplan_argv = [
        "flowplan",
        str(args.goal),
        "--root",
        str(repo),
        "--profile",
        "code-change",
        "--out",
        str(workflow_plan_path),
        "--role-lanes-out",
        str(role_lane_plan_path),
        "--lane-adapter",
        str(args.adapter),
        "--role-lane-tier",
        str(args.role_lane_tier),
        "--model-policy",
        "default",
        "--rolepack-file",
        str(rolepack_path),
        "--json",
    ]
    if args.verification_only:
        flowplan_argv += ["--lane-intent", "verification-only"]
    flowplan_code, flowplan_payload, flowplan_error = _invoke_orro_flow_phase(
        flowplan_argv
    )
    if flowplan_code != 0:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="flowplan",
            run_dir=run_dir,
            phases=phases,
            error=_orro_flow_phase_error(
                phase="flowplan",
                argv=flowplan_argv,
                payload=flowplan_payload,
                fallback_message=flowplan_error,
            ),
        )
    phases.append(
        {
            "phase": "flowplan",
            "status": "ok",
            "artifact": {
                "workflow_plan": str(workflow_plan_path),
                "role_lane_plan": str(role_lane_plan_path),
                "rolepack": str(rolepack_path),
            },
        }
    )

    proofrun_argv = [
        "proofrun",
        str(args.goal),
        "--repo",
        str(repo),
        "--home",
        str(home),
        "--workflow-plan",
        str(workflow_plan_path),
        "--role-lane-plan",
        str(role_lane_plan_path),
        "--adapter",
        str(args.adapter),
        "--runner-sandbox",
        str(runner_sandbox),
        "--run-dir",
        str(run_dir),
        "--json",
    ]
    if args.allow_reference_adapter:
        proofrun_argv.append("--allow-reference-adapter")
    proofrun_code, proofrun_payload, proofrun_error = _invoke_orro_flow_phase(
        proofrun_argv
    )
    if proofrun_code != 0:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="proofrun",
            run_dir=run_dir,
            phases=phases,
            error=_orro_flow_phase_error(
                phase="proofrun",
                argv=proofrun_argv,
                payload=proofrun_payload,
                fallback_message=proofrun_error,
            ),
        )
    phases.append(
        {
            "phase": "proofrun",
            "status": "ok",
            "artifact": str(run_dir / "team-ledger.json"),
        }
    )

    proofcheck_argv = [
        "proofcheck",
        "--evidence-dir",
        str(run_dir),
        "--home",
        str(home),
        "--out",
        str(proofcheck_path),
        "--json",
    ]
    proofcheck_code, proofcheck_payload, proofcheck_error = _invoke_orro_flow_phase(
        proofcheck_argv
    )
    if proofcheck_code != 0:
        return _emit_orro_flow_blocker(
            args,
            blocked_phase="proofcheck",
            run_dir=run_dir,
            phases=phases,
            error=_orro_flow_phase_error(
                phase="proofcheck",
                argv=proofcheck_argv,
                payload=proofcheck_payload,
                fallback_message=proofcheck_error,
            ),
        )
    phases.append(
        {
            "phase": "proofcheck",
            "status": "ok",
            "artifact": str(proofcheck_path),
        }
    )
    decision = (
        str(proofcheck_payload.get("decision", "blocked"))
        if isinstance(proofcheck_payload, dict)
        else "blocked"
    )
    result = {
        "kind": "orro-flow-result",
        "decision": decision,
        "run_dir": str(run_dir),
        "verdict": str(proofcheck_path),
        "runner_sandbox": str(runner_sandbox),
        "phases": phases,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def _invoke_orro_flow_phase(argv: list[str]) -> tuple[int, object, str]:
    try:
        from witnessd.cli._output import _invoke_cli_capture

        code, stdout, stderr = _invoke_cli_capture(argv)
    except Exception as exc:  # noqa: BLE001 - flow must never leak a phase traceback
        return 1, {}, str(exc)
    return code, _json_or_text(stdout), stderr.strip()


def _orro_flow_phase_error(
    *,
    phase: str,
    argv: list[str],
    payload: object,
    fallback_message: str,
) -> dict[str, object]:
    existing = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(existing, dict):
        error: dict[str, object] = dict(existing)
    else:
        error = {
            "code": f"ERR_ORRO_FLOW_{phase.upper()}_BLOCKED",
            "message": fallback_message or f"{phase} blocked",
        }
    error.setdefault("reason", fallback_message or f"{phase} returned a nonzero status")
    error.setdefault("required_input_or_grant", f"resolve the reported {phase} blocker")
    error.setdefault("next_command", shlex.join(["python3", "-m", "witnessd", *argv]))
    return error


def _emit_orro_flow_blocker(
    args: argparse.Namespace,
    *,
    blocked_phase: str,
    run_dir: Path | None,
    phases: list[dict[str, object]],
    error: dict[str, object],
) -> int:
    payload = {
        "kind": "orro-flow-result",
        "decision": "blocked",
        "blocked_phase": blocked_phase,
        "run_dir": str(run_dir) if run_dir is not None else None,
        "error": error,
        "phases": phases,
    }
    print(json.dumps(payload, sort_keys=True))
    return 2


def _orro_flow_flowplan_command(
    *,
    goal: str,
    repo: Path,
    workflow_plan_path: Path,
    role_lane_plan_path: Path,
    rolepack_path: Path,
    adapter: str,
    tier: str,
    verification_only: bool = False,
) -> str:
    argv = [
        "python3",
        "-m",
        "witnessd",
        "flowplan",
        goal,
        "--root",
        str(repo),
        "--profile",
        "code-change",
        "--out",
        str(workflow_plan_path),
        "--role-lanes-out",
        str(role_lane_plan_path),
        "--lane-adapter",
        adapter,
        "--role-lane-tier",
        tier,
        "--model-policy",
        "default",
        "--rolepack-file",
        str(rolepack_path),
        "--json",
    ]
    if verification_only:
        argv += ["--lane-intent", "verification-only"]
    return shlex.join(argv)
