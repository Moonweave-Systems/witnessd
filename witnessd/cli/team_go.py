from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from witnessd.cli._output import (
    _invoke_cli_capture,
    _json_or_text,
    _structured_error,
    _write_json_file,
)


def _fill_interactive_team_init_args(args: argparse.Namespace) -> None:
    role = input("runner role (role_id:adapter[:model]) [runner:codex]: ").strip()
    if not role:
        role = "runner:codex"
    scope = input("write scope [orro/**]: ").strip()
    args.role = [role]
    args.write_scope = [scope or "orro/**"]


def _cmd_team_go(args: argparse.Namespace) -> int:
    from witnessd.orro_workstyle import advise_workstyle
    from witnessd.orro_team_surface import (
        apply_task_prompt_to_role_lane_plan,
        verdict_has_no_work_error,
    )
    from witnessd.orro_workflow import (
        ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
        OrroWorkflowError,
        assert_role_lane_prompts_explicit,
        summarize_executable_lanes,
        validate_role_lane_plan,
    )
    from witnessd.role_capability import RolepackError, default_rolepack_for_profile

    repo = Path(args.repo).resolve(strict=False)
    home = Path(
        args.home or os.environ.get("WITNESSD_HOME") or (repo / ".witnessd")
    ).resolve(strict=False)
    run_dir = (
        Path(args.run_dir).resolve(strict=False)
        if args.run_dir
        else home
        / "runs"
        / f"team-go-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    workflow_plan_path = run_dir / "workflow-plan.json"
    role_lane_plan_path = run_dir / "role-lane-plan.json"
    report_path = run_dir / "orro-report.json"
    proofcheck_path = run_dir / "proofcheck-verdict.json"
    routing_decision_path = run_dir / "moonweave-routing-decision.json"
    task = args.task or args.goal

    advice = advise_workstyle(str(args.goal), repo=repo, home=home)
    selected_profile = args.profile or str(advice["recommended_profile"])
    profile_source = "manual" if args.profile else "advise"
    selected_rolepack: str | None = None
    rolepack_source = "manual-team" if args.team else "profile-default"
    try:
        if not args.team:
            selected_rolepack = default_rolepack_for_profile(selected_profile)
    except RolepackError as exc:
        routing_decision = _team_go_routing_decision(
            goal=str(args.goal),
            advice=advice,
            chosen_profile=selected_profile,
            chosen_rolepack=None,
            profile_source=profile_source,
            rolepack_source=rolepack_source,
            team_path=None,
        )
        _write_json_file(routing_decision_path, routing_decision)
        return _emit_team_go_result(
            args,
            {
                "kind": "orro-team-go-result",
                "status": "blocked",
                "stage": "routing",
                "run_dir": str(run_dir),
                "routing_decision": routing_decision,
                "routing_decision_path": str(routing_decision_path),
                "message": exc.message,
                "can_change_evidence_verdict": False,
            },
            code=1,
        )

    routing_decision = _team_go_routing_decision(
        goal=str(args.goal),
        advice=advice,
        chosen_profile=selected_profile,
        chosen_rolepack=selected_rolepack or str(Path(args.team).resolve(strict=False)),
        profile_source=profile_source,
        rolepack_source=rolepack_source,
        team_path=str(Path(args.team).resolve(strict=False)) if args.team else None,
    )
    _write_json_file(routing_decision_path, routing_decision)

    flow_argv = [
        "flowplan",
        args.goal,
        "--root",
        str(repo),
        "--profile",
        selected_profile,
        "--out",
        str(workflow_plan_path),
        "--role-lanes-out",
        str(role_lane_plan_path),
        "--role-lane-tier",
        args.role_lane_tier,
        "--json",
    ]
    if args.team:
        flow_argv.extend(["--team", str(Path(args.team).resolve(strict=False))])
    elif selected_rolepack:
        flow_argv.extend(["--rolepack", selected_rolepack, "--model-policy", "default"])

    flow_code, flow_stdout, flow_stderr = _invoke_cli_capture(flow_argv)
    if flow_code != 0:
        flow_error_payload = _json_or_text(flow_stdout)
        flow_error = (
            flow_error_payload.get("error")
            if isinstance(flow_error_payload, dict)
            and isinstance(flow_error_payload.get("error"), dict)
            else None
        )
        actionable_error = None
        if (
            isinstance(flow_error, dict)
            and flow_error.get("code") == "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED"
        ):
            rule_matches = advice.get("rule_matches")
            reason = (
                str(rule_matches[0])
                if isinstance(rule_matches, list) and rule_matches
                else str(
                    flow_error.get("message", "role capability adapter grant blocked")
                )
            )
            required = (
                "a human-reviewed --team <rolepack.json> whose runner grant "
                "includes the selected adapter"
            )
            next_command = (
                "python3 -m orro team go "
                f"{shlex.quote(str(args.goal))} --repo {shlex.quote(str(repo))} "
                f"--home {shlex.quote(str(home))} --team <rolepack.json> --json"
            )
            actionable_error = _structured_error(
                code=str(flow_error["code"]),
                message=str(flow_error.get("message", "flowplan failed")),
                reason=reason,
                required_input_or_grant=required,
                next_command=next_command,
            )
        return _emit_team_go_result(
            args,
            {
                "kind": "orro-team-go-result",
                "status": "blocked",
                "stage": "flowplan",
                "run_dir": str(run_dir),
                "routing_decision": routing_decision,
                "routing_decision_path": str(routing_decision_path),
                "message": "flowplan failed",
                "stderr": flow_stderr,
                "stdout": flow_stdout,
                **({"error": actionable_error} if actionable_error else {}),
                "can_change_evidence_verdict": False,
            },
            code=flow_code,
        )

    role_lane_plan = json.loads(role_lane_plan_path.read_text(encoding="utf-8"))
    patch_result = apply_task_prompt_to_role_lane_plan(role_lane_plan, task=task)
    patched_role_lane_plan = patch_result["role_lane_plan"]
    try:
        if patch_result["placeholder_count"] > patch_result["patched_count"]:
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
                "one or more role-lane placeholder prompts were not replaced",
            )
        validate_role_lane_plan(patched_role_lane_plan)
        assert_role_lane_prompts_explicit(patched_role_lane_plan)
    except OrroWorkflowError as exc:
        return _emit_team_go_result(
            args,
            {
                "kind": "orro-team-go-result",
                "status": "blocked",
                "stage": "role-lane-plan",
                "run_dir": str(run_dir),
                "routing_decision": routing_decision,
                "routing_decision_path": str(routing_decision_path),
                "message": str(exc),
                "error": {"code": exc.code, "message": str(exc)},
                "can_change_evidence_verdict": False,
            },
            code=2,
        )
    execution_summary = summarize_executable_lanes(patched_role_lane_plan["lanes"])
    single_lane_policy_selection = execution_summary["lane_count"] == 1 and any(
        isinstance(lane, dict) and lane.get("model_source") == "model-policy"
        for lane in patched_role_lane_plan["lanes"]
    )
    reference_adapter_lanes = _team_go_reference_adapter_lanes(patched_role_lane_plan)
    reference_adapter = bool(reference_adapter_lanes)
    reference_warning = _team_go_reference_adapter_warning(reference_adapter_lanes)
    if reference_warning is not None:
        _write_json_file(
            run_dir / "moonweave-reference-adapter-warning.json", reference_warning
        )
    if reference_adapter and not args.allow_reference_adapter:
        return _emit_team_go_result(
            args,
            {
                "kind": "orro-team-go-result",
                "status": "blocked",
                "stage": "reference-adapter",
                "run_dir": str(run_dir),
                "workflow_plan": str(workflow_plan_path),
                "role_lane_plan": str(role_lane_plan_path),
                "routing_decision": routing_decision,
                "routing_decision_path": str(routing_decision_path),
                "reference_adapter": True,
                "not_real_ai_work": True,
                "reference_adapter_lanes": reference_adapter_lanes,
                "reference_adapter_warning": reference_warning,
                **execution_summary,
                "message": (
                    "shell reference adapter runner lane is not real AI work; "
                    "pass --allow-reference-adapter only for intentional script/test runs"
                ),
                "can_change_evidence_verdict": False,
            },
            code=2,
        )
    role_lane_plan_path.write_text(
        json.dumps(patched_role_lane_plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    prompt_patch = {
        key: value for key, value in patch_result.items() if key != "role_lane_plan"
    }

    proofrun_code, proofrun_stdout, proofrun_stderr = _invoke_cli_capture(
        [
            "proofrun",
            args.goal,
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--workflow-plan",
            str(workflow_plan_path),
            "--role-lane-plan",
            str(role_lane_plan_path),
            "--run-dir",
            str(run_dir),
            "--max-parallel",
            str(args.max_parallel),
            "--codex-binary",
            args.codex_binary,
            "--claude-binary",
            args.claude_binary,
            "--agy-binary",
            args.agy_binary,
            "--gemini-binary",
            args.gemini_binary,
            "--opencode-binary",
            args.opencode_binary,
        ]
        + (["--fail-fast"] if args.fail_fast else [])
        + (["--allow-reference-adapter"] if args.allow_reference_adapter else [])
        + (["--roadmap-item", args.roadmap_item] if args.roadmap_item else [])
        + (["--roadmap-step", args.roadmap_step] if getattr(args, "roadmap_step", None) else [])
    )
    proofrun_payload = _json_or_text(proofrun_stdout)
    if proofrun_code != 0:
        report_payload = _write_team_go_report(run_dir, home, report_path)
        timeout_guidance = (
            proofrun_payload.get("timeout_guidance", [])
            if isinstance(proofrun_payload, dict)
            else []
        )
        no_work = verdict_has_no_work_error(
            _load_json_if_exists(run_dir / "team-ledger-verdict.json")
        ) or verdict_has_no_work_error(
            _load_json_if_exists(run_dir / "team-ledger.json")
        )
        return _emit_team_go_result(
            args,
            {
                "kind": "orro-team-go-result",
                "status": "blocked",
                "stage": "proofrun",
                "run_dir": str(run_dir),
                "workflow_plan": str(workflow_plan_path),
                "role_lane_plan": str(role_lane_plan_path),
                "report": str(report_path) if report_path.exists() else None,
                "proofrun": proofrun_payload,
                "report_payload": report_payload,
                "prompt_patch": prompt_patch,
                "routing_decision": routing_decision,
                "routing_decision_path": str(routing_decision_path),
                "reference_adapter": reference_adapter,
                "not_real_ai_work": reference_adapter,
                "reference_adapter_lanes": reference_adapter_lanes,
                "reference_adapter_warning": reference_warning,
                "no_work_detected": no_work,
                "timeout_guidance": timeout_guidance,
                **execution_summary,
                "message": (
                    "proofrun lane did not touch files; execution evidence is blocked"
                    if no_work
                    else str(timeout_guidance[0])
                    if isinstance(timeout_guidance, list) and timeout_guidance
                    else "proofrun failed; proofcheck was not run"
                ),
                "stderr": proofrun_stderr,
                "can_change_evidence_verdict": False,
            },
            code=1,
        )

    proofcheck_code, proofcheck_stdout, proofcheck_stderr = _invoke_cli_capture(
        [
            "proofcheck",
            str(run_dir),
            "--home",
            str(home),
            "--out",
            str(proofcheck_path),
            "--json",
        ]
    )
    proofcheck_payload = _json_or_text(proofcheck_stdout)
    report_payload = _write_team_go_report(run_dir, home, report_path)
    status = "complete" if proofcheck_code == 0 else "blocked"
    return _emit_team_go_result(
        args,
        {
            "kind": "orro-team-go-result",
            "status": status,
            "stage": "complete" if proofcheck_code == 0 else "proofcheck",
            "run_dir": str(run_dir),
            "workflow_plan": str(workflow_plan_path),
            "role_lane_plan": str(role_lane_plan_path),
            "team_ledger": str(run_dir / "team-ledger.json"),
            "team_ledger_verdict": str(run_dir / "team-ledger-verdict.json"),
            "proofcheck_verdict": str(proofcheck_path),
            "report": str(report_path),
            "proofrun": proofrun_payload,
            "proofcheck": proofcheck_payload,
            "report_payload": report_payload,
            "prompt_patch": prompt_patch,
            "routing_decision": routing_decision,
            "routing_decision_path": str(routing_decision_path),
            "reference_adapter": reference_adapter,
            "not_real_ai_work": reference_adapter,
            "reference_adapter_lanes": reference_adapter_lanes,
            "reference_adapter_warning": reference_warning,
            "no_work_detected": False,
            **execution_summary,
            "message": (
                (
                    "reference shell adapter run, proofcheck, and report completed; "
                    "this is not real AI work"
                )
                if proofcheck_code == 0 and reference_adapter
                else (
                    "single-lane policy-selected run, proofcheck, and report completed"
                    if proofcheck_code == 0 and single_lane_policy_selection
                    else "single-lane execution, proofcheck, and report completed"
                    if proofcheck_code == 0 and execution_summary["lane_count"] == 1
                    else "multi-lane run, proofcheck, and report completed"
                    if proofcheck_code == 0
                    else "proofcheck failed"
                )
            ),
            "stderr": proofcheck_stderr,
            "can_change_evidence_verdict": False,
        },
        code=0 if proofcheck_code == 0 else 1,
    )


def _team_go_reference_adapter_lanes(
    role_lane_plan: dict[str, object],
) -> list[dict[str, object]]:
    lanes = role_lane_plan.get("lanes")
    if not isinstance(lanes, list):
        return []
    reference_lanes: list[dict[str, object]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        if (
            lane.get("phase") == "proofrun"
            and lane.get("may_execute") is True
            and lane.get("adapter") == "shell"
        ):
            checks = lane.get("check_commands")
            if (
                lane.get("lane_intent") == "verification-only"
                and isinstance(checks, list)
                and checks
            ):
                continue
            commands = lane.get("commands")
            if (
                lane.get("lane_intent") == "implementation"
                and isinstance(commands, list)
                and commands
            ):
                continue
            reference_lanes.append(
                {
                    "lane_id": lane.get("lane_id"),
                    "role_id": lane.get("role_id"),
                    "adapter": "shell",
                    "phase": "proofrun",
                    "runner_kind": "manual",
                    "reference_adapter": True,
                    "not_real_ai_work": True,
                }
            )
    return reference_lanes


def _team_go_reference_adapter_warning(
    reference_lanes: list[dict[str, object]],
) -> dict[str, object] | None:
    if not reference_lanes:
        return None
    return {
        "kind": "moonweave-reference-adapter-warning",
        "schema_version": "0.1",
        "reference_adapter": True,
        "not_real_ai_work": True,
        "reference_adapter_lanes": reference_lanes,
        "message": (
            "shell proofrun lanes are reference/script lanes with manual runner receipts; "
            "they are not AI model execution"
        ),
        "can_change_evidence_verdict": False,
        "boundary": {
            "advisory_only": True,
            "raises_assurance": False,
            "depone_verifies": True,
        },
    }


def _team_go_routing_decision(
    *,
    goal: str,
    advice: dict[str, object],
    chosen_profile: str,
    chosen_rolepack: str | None,
    profile_source: str,
    rolepack_source: str,
    team_path: str | None,
) -> dict[str, object]:
    return {
        "kind": "moonweave-routing-decision",
        "schema_version": "0.1",
        "goal": goal,
        "judged_task_class": advice.get("task_class"),
        "chosen_profile": chosen_profile,
        "chosen_rolepack": chosen_rolepack,
        "profile_source": profile_source,
        "rolepack_source": rolepack_source,
        "team_path": team_path,
        "rule_matches": advice.get("rule_matches", []),
        "reasons": advice.get("reasons", []),
        "source": "advise",
        "can_change_evidence_verdict": False,
        "boundary": {
            "advisory_only": True,
            "raises_assurance": False,
            "depone_verifies": True,
        },
    }


def _load_json_if_exists(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_team_go_report(run_dir: Path, home: Path, report_path: Path) -> object:
    code, stdout, _stderr = _invoke_cli_capture(
        [
            "orro-report",
            str(run_dir),
            "--home",
            str(home),
            "--out",
            str(report_path),
            "--json",
        ]
    )
    payload = _json_or_text(stdout)
    if isinstance(payload, dict):
        payload.setdefault("exit_code", code)
    return payload


def _emit_team_go_result(
    args: argparse.Namespace,
    payload: dict[str, object],
    *,
    code: int,
) -> int:
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        error = payload.get("error")
        if isinstance(error, dict) and error.get("code"):
            print(str(error["code"]), file=sys.stderr)
            if error.get("next_command"):
                print(
                    f"{error.get('message', payload.get('message', 'blocked'))} "
                    f"Next: {error['next_command']}",
                    file=sys.stderr,
                )
        surface = "ORRO run" if payload.get("lane_count") == 1 else "ORRO team go"
        print(f"{surface}: {payload.get('status')} ({payload.get('stage')})")
        print(payload.get("message", ""))
        if payload.get("run_dir"):
            print(f"run_dir: {payload['run_dir']}")
        if payload.get("proofcheck_verdict"):
            print(f"proofcheck_verdict: {payload['proofcheck_verdict']}")
        if payload.get("report"):
            print(f"report: {payload['report']}")
    return code


def _apply_lane_prompt_files(lane_specs: list[dict], entries: list[str]) -> None:
    specs_by_id = {str(spec.get("lane_id")): spec for spec in lane_specs}
    for entry in entries:
        lane_id, sep, prompt_path = entry.partition("=")
        lane_id = lane_id.strip()
        if sep != "=" or not lane_id or not prompt_path:
            raise ValueError("ERR_TEAM_RUN_LANE_PROMPT_FILE_FORMAT")
        if lane_id not in specs_by_id:
            raise ValueError("ERR_TEAM_RUN_LANE_PROMPT_FILE_UNKNOWN_LANE")
        spec = specs_by_id[lane_id]
        if "prompt" not in spec:
            raise ValueError("ERR_TEAM_RUN_LANE_PROMPT_FILE_NON_ADAPTER")
        spec["prompt"] = Path(prompt_path).read_text(encoding="utf-8")
