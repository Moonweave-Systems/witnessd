from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

from witnessd.cli._output import _emit_orro_error


def _cmd_plan(args: argparse.Namespace) -> int:
    from witnessd.adapter_run import LaneBlocked, run_adapter_lane
    from witnessd.adapters.codex import CodexAdapterError
    from witnessd.orro_workflow import (
        OrroWorkflowError,
        compile_role_lane_plan,
        compile_workflow_plan,
        summarize_executable_lanes,
        write_role_lane_plan,
    )
    from witnessd.planner import (
        PlannerError,
        parse_draft_packets,
        plan_heuristic,
        seal_plan,
    )

    draft_events: list[dict] = []
    packets: list[dict] | None = None
    root = os.path.abspath(args.root)
    workflow_plan = None
    role_lane_plan_ref: dict[str, object] | None = None

    if getattr(args, "profile", None):
        try:
            workflow_plan = compile_workflow_plan(
                goal=args.goal,
                profile=args.profile,
                lane_intent=getattr(args, "lane_intent", None),
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(
                args,
                code=exc.code,
                message="unknown ORRO workflow profile",
            )
            return 2

    if getattr(args, "check", None) and not getattr(args, "role_lanes_out", None):
        _emit_orro_error(
            args,
            code="ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED",
            message=(
                "--check requires --role-lanes-out with --profile verification-only"
            ),
        )
        return 2

    if args.draft_adapter:
        draft_root = f"{root.rstrip(os.sep)}-witnessd-plan-draft"
        draft_out = args.draft_out or os.path.join(draft_root, "evidence")
        try:
            result = run_adapter_lane(
                root=root,
                sandbox=root,
                adapter=args.draft_adapter,
                task_id="w11-plan-draft",
                prompt=_draft_prompt(args.goal),
                arm="direct",
                tier=args.tier,
                is_supported=lambda _model: True,
                budget={
                    "max_tokens": args.max_tokens,
                    "max_usd": args.max_usd,
                    "max_depth": args.max_depth,
                },
                predicted_tokens=args.predicted_tokens,
                predicted_usd=args.predicted_usd,
                codex_binary=args.codex_binary,
                claude_binary=args.claude_binary,
                agy_binary=args.agy_binary,
                gemini_binary=args.gemini_binary,
                opencode_binary=args.opencode_binary,
                evidence_dir=draft_out,
                state_root=draft_root,
                allowed_touched_files=["witnessd-plan-draft.txt"],
            )
            transcript = (
                Path(result["evidence_dir"]).resolve(strict=False).parent
                / "adapter-transcript.txt"
            )
            packets = parse_draft_packets(transcript.read_text(encoding="utf-8"))
            draft_events.append(
                {
                    "adapter": args.draft_adapter,
                    "status": "adopted",
                    "evidence_dir": result["evidence_dir"],
                }
            )
        except (LaneBlocked, PlannerError, OSError, CodexAdapterError) as exc:
            reason = str(exc).split(":", 1)[0]
            draft_events.append(
                {
                    "adapter": args.draft_adapter,
                    "status": "fallback",
                    "reason": reason,
                }
            )

    if packets is None:
        packets = plan_heuristic(args.goal, seed=args.seed, root=root)

    sealed = seal_plan(packets, goal=args.goal)
    payload: dict[str, object] = {"sealed_plan": sealed, "draft_events": draft_events}
    if workflow_plan is not None:
        payload["workflow_plan"] = workflow_plan
    if getattr(args, "role_lanes_out", None):
        if workflow_plan is None:
            workflow_plan = compile_workflow_plan(
                goal=args.goal,
                profile="code-change",
                lane_intent=getattr(args, "lane_intent", None),
            )
            payload["workflow_plan"] = workflow_plan
        rolepack: dict[str, object] | None = None
        try:
            from witnessd.model_policy import DEFAULT_MODEL_POLICY
            from witnessd.role_capability import (
                RolepackError,
                load_rolepack_file,
                resolve_rolepack,
            )

            selected_rolepack_inputs = [
                value
                for value in (
                    args.rolepack,
                    args.rolepack_file,
                    getattr(args, "team", None),
                )
                if value
            ]
            raw_write_scope = list(getattr(args, "write_scope", []))
            write_scope = [scope for scope in raw_write_scope if scope]
            if raw_write_scope and selected_rolepack_inputs:
                raise RolepackError(
                    "ERR_ORRO_ROLEPACK_CONFLICT",
                    "--write-scope, --rolepack, --rolepack-file, and --team are mutually exclusive",
                )
            if raw_write_scope and not write_scope:
                raise ValueError("--write-scope requires a non-empty glob")
            if len(selected_rolepack_inputs) > 1:
                raise RolepackError(
                    "ERR_ORRO_ROLEPACK_CONFLICT",
                    "--rolepack, --rolepack-file, and --team are mutually exclusive",
                )
            if write_scope and workflow_plan.get("profile") == "code-change":
                from witnessd.orro_team_surface import build_rolepack_scaffold

                rolepack = build_rolepack_scaffold(
                    template=None,
                    roles=[f"runner:{args.lane_adapter}"],
                    write_scope=write_scope,
                )
            else:
                rolepack = (
                    load_rolepack_file(args.team or args.rolepack_file)
                    if args.team or args.rolepack_file
                    else resolve_rolepack(args.rolepack)
                )

            role_lane_plan = compile_role_lane_plan(
                workflow_plan=workflow_plan,
                lane_adapter=args.lane_adapter,
                tier=args.role_lane_tier,
                lane_timeout_seconds=getattr(args, "lane_timeout_seconds", None),
                policy=DEFAULT_MODEL_POLICY if args.model_policy == "default" else None,
                rolepack=rolepack,
                check_commands=getattr(args, "check", None),
            )
            role_lane_plan_ref = write_role_lane_plan(
                Path(args.role_lanes_out).resolve(strict=False),
                role_lane_plan,
            )
        except RolepackError as exc:
            _emit_orro_error(args, code=exc.code, message=exc.message)
            return 1
        except OrroWorkflowError as exc:
            details = _flowplan_role_lane_error_details(
                args,
                code=exc.code,
                message=str(exc),
                rolepack=rolepack,
            )
            _emit_orro_error(
                args,
                code=exc.code,
                message=str(exc),
                **(details or {}),
            )
            return 1
        except ValueError as exc:
            _emit_orro_error(
                args,
                code="ERR_ORRO_FLOWPLAN_WRITE_SCOPE_INVALID",
                message=str(exc),
                reason="--write-scope values must be non-empty rolepack write_scope globs",
                required_input_or_grant="--write-scope '<glob>' (repeatable)",
            )
            return 1
        payload["role_lane_plan"] = role_lane_plan_ref
        payload.update(summarize_executable_lanes(role_lane_plan["lanes"]))
    if getattr(args, "out", None):
        out_path = Path(args.out).resolve(strict=False)
        try:
            out_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _emit_orro_error(
                args,
                code="ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED",
                message=str(exc),
            )
            return 1
    print(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _draft_prompt(goal: str) -> str:
    return (
        "Return only JSON with a packets array of witnessd LanePacket objects for "
        f"this goal: {goal}"
    )
def _flowplan_role_lane_error_details(
    args: argparse.Namespace,
    *,
    code: str,
    message: str,
    rolepack: dict[str, object] | None,
) -> dict[str, str] | None:
    if code == "ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED":
        flowplan_command = (
            "python3 -m orro flowplan "
            f"{shlex.quote(str(args.goal))} --root {shlex.quote(str(args.root))} "
            f"--profile {shlex.quote(str(args.profile or 'code-change'))} "
            f"--role-lanes-out {shlex.quote(str(args.role_lanes_out))} "
            "--rolepack-file rolepack.json --model-policy default"
        )
        return {
            "reason": (
                "code-change proofrun lanes need a concrete write_scope from the rolepack"
            ),
            "required_input_or_grant": (
                "a rolepack granting the role's write_scope"
            ),
            "next_command": (
                "python3 -m orro team init --template developer "
                "--write-scope '<glob>' --out rolepack.json && "
                f"{flowplan_command}"
            ),
        }
    if code != "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED":
        return None

    grants = rolepack.get("grants") if isinstance(rolepack, dict) else None
    grant_rows = [grant for grant in grants if isinstance(grant, dict)] if isinstance(grants, list) else []
    role_ids = sorted(
        str(grant["role_id"])
        for grant in grant_rows
        if isinstance(grant.get("role_id"), str)
    )
    role_id = next(
        (candidate for candidate in role_ids if f"role_id={candidate!r}" in message),
        role_ids[0] if len(role_ids) == 1 else "<role>",
    )
    matching_grant = next(
        (grant for grant in grant_rows if grant.get("role_id") == role_id),
        None,
    )
    adapters = (
        matching_grant.get("adapters")
        if isinstance(matching_grant, dict)
        else None
    )
    granted_adapters = sorted(
        str(adapter) for adapter in adapters if isinstance(adapter, str)
    ) if isinstance(adapters, list) else []
    resolved_adapter = next(
        (
            adapter
            for adapter in ("shell", "codex", "claude", "agy", "gemini", "opencode")
            if f"adapter {adapter!r}" in message
        ),
        str(args.lane_adapter),
    )
    reason = (
        f"resolved adapter {resolved_adapter!r} is not granted for role_id={role_id!r}; "
        f"the rolepack has granted adapters {granted_adapters!r} for that role and grants "
        f"role_ids {role_ids!r}"
    )
    if resolved_adapter == "shell" and any(
        adapter != "shell" for adapter in granted_adapters
    ):
        reason += (
            "; pass --model-policy default (routes to the granted adapter) or ensure "
            "the rolepack grants the adapter you intend"
        )
    rolepack_arg = (
        f"--rolepack-file {shlex.quote(str(args.rolepack_file))}"
        if args.rolepack_file
        else (
            f"--team {shlex.quote(str(args.team))}"
            if getattr(args, "team", None)
            else f"--rolepack {shlex.quote(str(args.rolepack))}"
        )
    )
    next_command = (
        "python3 -m orro flowplan "
        f"{shlex.quote(str(args.goal))} --root {shlex.quote(str(args.root))} "
        f"--profile {shlex.quote(str(args.profile or 'code-change'))} "
        f"--role-lanes-out {shlex.quote(str(args.role_lanes_out))} "
        f"{rolepack_arg} --model-policy default; or scaffold a matching rolepack: "
        "python3 -m orro team init --role <role> --write-scope '<glob>' "
        "--out rolepack.json"
    )
    return {
        "reason": reason,
        "required_input_or_grant": (
            f"a rolepack granting adapter {resolved_adapter!r} for role_id={role_id!r}; "
            f"granted adapters: {granted_adapters!r}; role_ids: {role_ids!r}"
        ),
        "next_command": next_command,
    }
