from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from witnessd.cli._output import _emit_orro_error, _write_json_file
from witnessd.observer import ObserverSeparationError, assert_separated
from witnessd.status import render_status


ERR_ORRO_REFERENCE_ADAPTER_REFUSED = "ERR_ORRO_REFERENCE_ADAPTER_REFUSED"
RUNNER_SANDBOX_DIRECTORY_REASON = (
    "--runner-sandbox is a filesystem directory where the adapter runner "
    "executes; it is not the host Codex sandbox_mode "
    "(read-only/workspace-write), not the observer out/log directory, and "
    "not controlled by the shell session start directory"
)


def _requested_signing_profile(args: argparse.Namespace) -> str | None:
    requested = getattr(args, "signing_profile", None)
    if getattr(args, "keyless", False):
        if requested not in {None, "keyless-fulcio-rekor"}:
            raise ValueError("ERR_WITNESSD_SIGNING_PROFILE_CONFLICT")
        return "keyless-fulcio-rekor"
    return requested


def _keyless_options(args: argparse.Namespace) -> dict[str, object]:
    return {
        "identity_token": getattr(args, "identity_token", None),
        "oauth_force_oob": bool(getattr(args, "oauth_force_oob", False)),
        "staging": bool(getattr(args, "staging", False)),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        signing_profile = _requested_signing_profile(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if getattr(args, "goal", None):
        if signing_profile == "keyless-fulcio-rekor":
            print(
                "ERR_WITNESSD_KEYLESS_TEAM_UNSUPPORTED: keyless signing is "
                "available for direct run/proofrun emission only; no keyless "
                "identity flow was started",
                file=sys.stderr,
            )
            return 2
        return _cmd_run_goal(args)

    # A sealed workflow/role-lane plan carries its own lane prompts, so proofrun
    # executes the plan (deriving prompts from its lanes and applying the
    # reference-adapter/placeholder guard) instead of demanding an ad-hoc prompt
    # via the direct-adapter path.
    if getattr(args, "workflow_plan", None) or getattr(args, "role_lane_plan", None):
        if signing_profile == "keyless-fulcio-rekor":
            print(
                "ERR_WITNESSD_KEYLESS_TEAM_UNSUPPORTED: keyless signing is "
                "available for direct run/proofrun emission only; no keyless "
                "identity flow was started",
                file=sys.stderr,
            )
            return 2
        return _cmd_run_goal(args)

    if args.adapter != "shell":
        return _cmd_run_adapter(args)

    if not args.runner_sandbox:
        print("ERR_WITNESSD_RUN_GOAL_OR_SANDBOX_REQUIRED", file=sys.stderr)
        print(f"message: {RUNNER_SANDBOX_DIRECTORY_REASON}", file=sys.stderr)
        print(
            "next_command: "
            + _proofrun_runner_sandbox_next_command(
                args,
                adapter="shell",
                prompt=list(args.command or []),
            ),
            file=sys.stderr,
        )
        return 2

    sandbox = os.path.abspath(args.runner_sandbox)
    if not os.path.isdir(sandbox):
        print("ERR_RUNTIME_SANDBOX_UNAVAILABLE", file=sys.stderr)
        return 2
    if not args.out or not args.log:
        print("ERR_OBSERVER_OUTPUT_REQUIRED", file=sys.stderr)
        return 2
    out_path = os.path.abspath(args.out)
    log_path = os.path.abspath(args.log)

    # Fail closed before capturing anything: both observer outputs must be
    # outside the runner sandbox.
    try:
        assert_separated(runner_sandbox=sandbox, out_path=out_path)
        assert_separated(runner_sandbox=sandbox, out_path=log_path)
    except ObserverSeparationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.command:
        print("ERR_NO_COMMAND", file=sys.stderr)
        return 2

    from witnessd.adapters.shell import run_shell_lane
    from witnessd.emitter import EmitterError, emit_lane_evidence
    from witnessd.fixture import (
        build_reference_adapter_fixture,
        build_shell_invocation,
    )
    from witnessd.privacy import (
        CAPTURE_PROFILE_REDACTED,
        build_redaction_context,
        build_pattern_scrub_manifest,
        redact_secrets_in,
        redact_value,
    )
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.dirname(out_path)
    keys_dir = args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys")
    keys_dir = os.path.abspath(keys_dir)
    os.makedirs(keys_dir, exist_ok=True)
    keypair_preexisted = all(
        os.path.isfile(os.path.join(keys_dir, name))
        for name in ("operator-ed25519.pem", "operator-ed25519.pub.pem")
    )
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)
    from witnessd.trust_anchor import (
        resolve_bundle_trust_anchor,
        resolve_trust_anchor,
    )

    trust_anchor = resolve_trust_anchor(
        runtime_public_key=Path(public_key_path),
        runtime_generated=not keypair_preexisted,
    )

    allowed_touched_files = list(args.allow or [])
    observed_command = (
        ["sh", "-c", args.command[0]] if len(args.command) == 1 else list(args.command)
    )
    commands = [observed_command]
    lane_result = run_shell_lane(sandbox=sandbox, commands=commands)
    redaction_context = None
    if args.capture_profile == CAPTURE_PROFILE_REDACTED:
        redaction_context = build_redaction_context(
            run_id=args.task_id,
            prompt=" ".join(args.command),
            paths=[*allowed_touched_files, *lane_result.get("touched_files", [])],
            worktree=sandbox,
        )
        lane_result = redact_value(lane_result, redaction_context)
        allowed_touched_files = list(
            redact_value(allowed_touched_files, redaction_context)
        )
    scrubbed_values, secret_findings = redact_secrets_in(
        {
            "lane_result": lane_result,
            "allowed_touched_files": allowed_touched_files,
            "runner_sandbox": str(redact_value(sandbox, redaction_context)),
        }
    )
    lane_result = scrubbed_values["lane_result"]
    allowed_touched_files = scrubbed_values["allowed_touched_files"]
    runner_sandbox = scrubbed_values["runner_sandbox"]
    redaction_manifest = build_pattern_scrub_manifest(
        run_id=args.task_id,
        capture_profile=args.capture_profile,
        findings=secret_findings,
        manifest=(
            redaction_context["manifest"] if redaction_context is not None else None
        ),
    )

    # The source fixture is the declared (A0) side; Depone requires a proper
    # agent-fabric-reference-adapter-fixture, not a placeholder.
    fixture = build_reference_adapter_fixture(build_shell_invocation(args.task_id))

    try:
        result = emit_lane_evidence(
            lane_result,
            evidence_dir,
            private_key_path,
            fixture=fixture,
            allowed_touched_files=allowed_touched_files,
            public_key_path=public_key_path,
            task_id=args.task_id,
            runner_sandbox=runner_sandbox,
            runtime_sandbox=sandbox,
            capture_profile=args.capture_profile,
            redaction_manifest=redaction_manifest,
            observer_output_path=out_path,
            transcript_path=log_path,
            signing_profile=signing_profile,
            keyless_options=_keyless_options(args),
        )
    except (EmitterError, OSError) as exc:
        print(f"ERR_OBSERVER_PERSIST_FAILED: {exc}", file=sys.stderr)
        return 1

    missing_sinks = [path for path in (out_path, log_path) if not os.path.isfile(path)]
    if missing_sinks:
        print(
            f"ERR_OBSERVER_PERSIST_FAILED: missing {', '.join(missing_sinks)}",
            file=sys.stderr,
        )
        return 1

    command_exit = result["receipt"]["exit_code"]
    if command_exit != 0:
        print(
            f"ERR_VERIFICATION_COMMAND_FAILED: exit {command_exit}",
            file=sys.stderr,
        )
        return 1

    pending = 1
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"evidence_dir: {evidence_dir}")
    from witnessd.cli.team_ops import _print_trust_anchor_summary

    trust_anchor = resolve_bundle_trust_anchor(
        result["bundle"], fallback=trust_anchor
    )
    _print_trust_anchor_summary(trust_anchor, candidate_assurance=result["assurance"])
    return 0


def _cmd_run_goal(args: argparse.Namespace) -> int:
    from witnessd.distribution import (
        ProvisionError,
        run_depone_team_ledger,
        validate_depone_pin,
    )
    from witnessd.eventlog import EventLog
    from witnessd.fanin import run_team
    from witnessd.orro_team_surface import apply_task_prompt_to_role_lane_plan
    from witnessd.orro_workflow import (
        ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
        ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX,
        OrroWorkflowError,
        assert_role_lane_prompts_explicit,
        assert_workflow_phase_allowed,
        load_workflow_plan,
        load_role_lane_plan,
        validate_role_lane_plan,
        write_workflow_plan_binding,
        write_role_lane_plan_binding,
        write_workflow_role_dispatch,
    )
    from witnessd.planner import dispatch, seal_plan
    from witnessd.orro_roadmap import (
        OrroRoadmapError,
        require_roadmap_item,
        require_roadmap_step,
        seal_roadmap_binding,
    )
    from witnessd.signing import gen_operator_keypair
    from witnessd.trust_anchor import resolve_trust_anchor

    repo = Path(args.repo or args.root or ".").resolve(strict=False)
    home = Path(
        args.home or os.environ.get("WITNESSD_HOME") or (repo / ".witnessd")
    ).resolve(strict=False)
    roadmap_item = getattr(args, "roadmap_item", None)
    roadmap_step = getattr(args, "roadmap_step", None)
    if roadmap_step is not None and roadmap_item is None:
        _emit_orro_error(args, code="ERR_ORRO_ROADMAP_STEP_REQUIRES_ITEM", message="--roadmap-step requires --roadmap-item")
        return 2
    if roadmap_item is not None:
        try:
            item = require_roadmap_item(repo, roadmap_item)
            if roadmap_step is not None:
                require_roadmap_step(repo, roadmap_item, roadmap_step, item=item)
        except OrroRoadmapError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2
    try:
        validate_depone_pin(home)
    except ProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2

    workflow_plan: dict[str, object] | None = None
    workflow_plan_source: Path | None = None
    role_lane_plan: dict[str, object] | None = None
    role_lane_plan_source: Path | None = None
    if getattr(args, "workflow_plan", None):
        workflow_plan_source = Path(args.workflow_plan).resolve(strict=False)
        try:
            workflow_plan = load_workflow_plan(
                workflow_plan_source, expected_goal=args.goal
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

    if getattr(args, "role_lane_plan", None):
        if workflow_plan is None:
            _emit_orro_error(
                args,
                code="ERR_ORRO_ROLE_LANE_PLAN_INVALID",
                message="--role-lane-plan requires --workflow-plan",
            )
            return 2
        role_lane_plan_source = Path(args.role_lane_plan).resolve(strict=False)
        try:
            role_lane_plan = load_role_lane_plan(
                role_lane_plan_source,
                workflow_plan=workflow_plan,
                require_explicit_prompts=False,
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

        task_value = (
            getattr(args, "task", None)
            or args.goal
            or workflow_plan.get("goal")
        )
        task = task_value if isinstance(task_value, str) and task_value.strip() else None
        lanes = role_lane_plan.get("lanes")
        placeholder_count = sum(
            1
            for lane in lanes
            if isinstance(lane, dict)
            and isinstance(lane.get("prompt"), str)
            and lane["prompt"].startswith(ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX)
        ) if isinstance(lanes, list) else 0
        if placeholder_count and task is None:
            _emit_orro_error(
                args,
                code=ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
                message="role-lane placeholder prompts could not be filled",
                reason=(
                    "the sealed plan's lane prompts are placeholders and no "
                    "task/goal was available to fill them"
                ),
                required_input_or_grant=(
                    "--task '<goal>' or a workflow-plan with a goal"
                ),
                next_command=(
                    "python3 -m orro proofrun --workflow-plan workflow-plan.json "
                    "--role-lane-plan role-lane-plan.json --task '<goal>' --json"
                ),
            )
            return 2
        patch_result = (
            apply_task_prompt_to_role_lane_plan(role_lane_plan, task=task or "")
            if placeholder_count
            else {
                "role_lane_plan": role_lane_plan,
                "patched_count": 0,
                "placeholder_count": 0,
            }
        )
        role_lane_plan = patch_result["role_lane_plan"]
        try:
            if patch_result["placeholder_count"] > patch_result["patched_count"]:
                raise OrroWorkflowError(
                    ERR_ORRO_ROLE_LANE_PLACEHOLDER_PROMPT,
                    "one or more role-lane placeholder prompts were not replaced",
                )
            validate_role_lane_plan(role_lane_plan)
            assert_role_lane_prompts_explicit(role_lane_plan)
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

    reference_fallback = args.cmd == "proofrun" and role_lane_plan is None

    if workflow_plan is not None:
        try:
            assert_workflow_phase_allowed(workflow_plan, "proofrun")
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

    from witnessd.cli.team_go import _team_go_reference_adapter_lanes

    reference_adapter_lanes = (
        _team_go_reference_adapter_lanes(role_lane_plan)
        if role_lane_plan is not None
        else []
    )
    if reference_adapter_lanes and not args.allow_reference_adapter:
        message = (
            "role-lane plan contains shell reference/placeholder proofrun lanes "
            "that are not real AI work; pass --allow-reference-adapter only for "
            "intentional script/test runs, or supply real-adapter lanes"
        )
        if args.json:
            _emit_orro_error(
                args,
                code=ERR_ORRO_REFERENCE_ADAPTER_REFUSED,
                message=message,
            )
        else:
            print(
                f"{ERR_ORRO_REFERENCE_ADAPTER_REFUSED}: {message}",
                file=sys.stderr,
            )
        return 2

    if reference_fallback and not args.allow_reference_adapter:
        code = "ERR_ORRO_PROOFRUN_NO_PLAN"
        message = (
            "proofrun requires an explicit workflow and role-lane plan pair or the "
            "--allow-reference-adapter opt-in; it will not run goal-unrelated "
            "placeholder work as if it satisfied the goal"
        )
        if args.json:
            _emit_orro_error(args, code=code, message=message)
        else:
            print(f"{code}: {message}", file=sys.stderr)
        return 2

    if args.run_dir:
        out_dir = Path(args.run_dir).resolve(strict=False)
    else:
        out_dir = (
            home
            / "runs"
            / f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    if roadmap_item is not None:
        try:
            seal_roadmap_binding(
                repo=repo,
                run_dir=out_dir,
                item_id=roadmap_item,
                step_id=roadmap_step,
            )
        except OrroRoadmapError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1

    workflow_plan_ref: dict[str, object] | None = None
    if workflow_plan is not None and workflow_plan_source is not None:
        try:
            workflow_plan_ref = write_workflow_plan_binding(
                plan=workflow_plan,
                source_path=workflow_plan_source,
                run_dir=out_dir,
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1

    role_lane_plan_ref: dict[str, object] | None = None
    if role_lane_plan is not None and role_lane_plan_source is not None:
        try:
            role_lane_plan_ref = write_role_lane_plan_binding(
                role_lane_plan=role_lane_plan,
                source_path=role_lane_plan_source,
                run_dir=out_dir,
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1

    execution_goal = (
        getattr(args, "task", None)
        or args.goal
        or (workflow_plan.get("goal") if workflow_plan is not None else None)
    )
    packets = (
        _role_lane_plan_packets(role_lane_plan)
        if role_lane_plan is not None
        else _default_w18_packets(execution_goal)
    )
    from witnessd.cli.team_go import _team_go_reference_adapter_warning

    reference_warning = (
        _proofrun_reference_adapter_warning(packets)
        if reference_fallback
        else _team_go_reference_adapter_warning(reference_adapter_lanes)
    )
    if reference_warning is not None:
        _write_json_file(
            out_dir / "moonweave-reference-adapter-warning.json",
            reference_warning,
        )
    sealed = seal_plan(packets, goal=execution_goal)
    sealed_path = out_dir / "sealed-plan.json"
    sealed_path.write_text(
        json.dumps(sealed, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    dispatch_log = EventLog(str(out_dir / "dispatch-log.jsonl"))
    for event in dispatch(sealed):
        dispatch_log.append(event)

    keys_dir = home / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(keys_dir, 0o700)
    keypair_preexisted = all(
        (keys_dir / name).is_file()
        for name in ("operator-ed25519.pem", "operator-ed25519.pub.pem")
    )
    private_key_path, public_key_path = gen_operator_keypair(str(keys_dir))
    trust_anchor = resolve_trust_anchor(
        home=home,
        runtime_public_key=Path(public_key_path),
        runtime_generated=not keypair_preexisted,
    )
    from witnessd.cli.team_ops import _lane_packet_to_run_team_spec

    lane_specs = (
        _role_lane_plan_team_specs(role_lane_plan, args)
        if role_lane_plan is not None
        else [
            _lane_packet_to_run_team_spec(packet, args) for packet in sealed["packets"]
        ]
    )
    run_team(
        lane_specs,
        repo_root=str(repo),
        out_dir=str(out_dir),
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        leader_objective=execution_goal,
        stop_rule="evidence-pending",
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
    )
    if reference_warning is not None:
        _stamp_reference_adapter_artifact(
            out_dir / "team-ledger.json",
            reference_warning,
        )
    verdict_path = out_dir / "team-ledger-verdict.json"
    try:
        verdict = run_depone_team_ledger(
            home=home,
            ledger_path=out_dir / "team-ledger.json",
            verdict_path=verdict_path,
            trusted_observer_public_key_file=trust_anchor.public_key_path,
        )
    except ProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    if reference_warning is not None:
        verdict.update(_reference_adapter_markers(reference_warning))
        _stamp_reference_adapter_artifact(verdict_path, reference_warning)
    payload = {
        "decision": verdict["decision"],
        "lane_count": verdict["lane_count"],
        "run_dir": str(out_dir),
        "sealed_plan": str(sealed_path),
        "team_ledger": str(out_dir / "team-ledger.json"),
        "team_ledger_verdict": str(verdict_path),
        "trust_anchor": trust_anchor.trust_anchor,
        "independent_trust_anchor": trust_anchor.independent,
    }
    timeout_guidance = _team_ledger_timeout_guidance(out_dir / "team-ledger.json")
    if timeout_guidance:
        payload["timeout_guidance"] = timeout_guidance
    if reference_warning is not None:
        payload.update(_reference_adapter_markers(reference_warning))
    if workflow_plan_ref is not None:
        payload["workflow_plan"] = workflow_plan_ref
    if role_lane_plan_ref is not None:
        payload["role_lane_plan"] = role_lane_plan_ref
    if workflow_plan is not None:
        try:
            payload["workflow_role_dispatch"] = write_workflow_role_dispatch(
                plan=workflow_plan,
                run_dir=out_dir,
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
    print(json.dumps(payload, sort_keys=True))
    return 0 if verdict["decision"] == "pass" else 1


def _team_ledger_timeout_guidance(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lanes = payload.get("lanes") if isinstance(payload, dict) else None
    if not isinstance(lanes, list):
        return []
    return [
        str(lane["guidance"])
        for lane in lanes
        if isinstance(lane, dict)
        and lane.get("blocked_reason")
        == "ERR_TEAM_LANE_TIMEOUT_COMMITTED_EVIDENCE_PENDING"
        and isinstance(lane.get("guidance"), str)
    ]


def _default_w18_packets(goal: str) -> list[dict]:
    budget = {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1}
    return [
        {
            "lane_id": "w18-lane-a",
            "adapter": "shell",
            "tier": "quick",
            "region": ["w18/lane-a.txt"],
            "prompt": f"Record W18 lane A evidence for {goal}",
            "budget": budget,
            "stop_rule": "evidence-pending",
        },
        {
            "lane_id": "w18-lane-b",
            "adapter": "shell",
            "tier": "quick",
            "region": ["w18/lane-b.txt"],
            "prompt": f"Record W18 lane B evidence for {goal}",
            "budget": budget,
            "stop_rule": "evidence-pending",
        },
    ]


def _proofrun_reference_adapter_warning(
    packets: list[dict],
) -> dict[str, object]:
    reference_lanes = [
        {
            "lane_id": packet["lane_id"],
            "adapter": "shell",
            "runner_kind": "manual",
            "reference_adapter": True,
            "not_real_ai_work": True,
            "placeholder_fallback": True,
        }
        for packet in packets
    ]
    return {
        "kind": "moonweave-reference-adapter-warning",
        "schema_version": "0.1",
        "reference_adapter": True,
        "not_real_ai_work": True,
        "placeholder_fallback": True,
        "reference_adapter_lanes": reference_lanes,
        "message": (
            "the deterministic W18 shell fallback writes reference fixture output; "
            "it does not perform the requested goal and is not real AI work"
        ),
        "can_change_evidence_verdict": False,
        "boundary": {
            "advisory_only": True,
            "raises_assurance": False,
            "depone_verifies": True,
        },
    }


def _reference_adapter_markers(warning: dict[str, object]) -> dict[str, bool]:
    return {
        "reference_adapter": bool(warning.get("reference_adapter")),
        "not_real_ai_work": bool(warning.get("not_real_ai_work")),
        "placeholder_fallback": bool(warning.get("placeholder_fallback")),
    }


def _stamp_reference_adapter_artifact(
    path: Path,
    warning: dict[str, object],
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    payload.update(_reference_adapter_markers(warning))
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _role_lane_plan_packets(role_lane_plan: dict[str, object] | None) -> list[dict]:
    if role_lane_plan is None:
        return []
    lanes = role_lane_plan.get("lanes", [])
    packets = []
    if not isinstance(lanes, list):
        return packets
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        if (
            lane.get("lane_intent") == "verification-only"
            and not list(lane.get("region") or [])
        ):
            continue
        packets.append(
            {
                "lane_id": lane["lane_id"],
                "adapter": lane["adapter"],
                "tier": lane["tier"],
                "region": list(lane["region"]),
                "prompt": lane["prompt"],
                "budget": dict(lane["budget"]),
                "stop_rule": "evidence-pending",
            }
        )
    return packets


def _role_lane_plan_team_specs(
    role_lane_plan: dict[str, object] | None,
    args: argparse.Namespace,
) -> list[dict]:
    if role_lane_plan is None:
        return []
    lanes = role_lane_plan.get("lanes", [])
    specs = []
    if not isinstance(lanes, list):
        return specs
    from witnessd.cli.team_ops import _default_team_lane_command

    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        adapter = str(lane["adapter"])
        region = list(lane["region"])
        if adapter == "shell":
            declared_commands = lane.get("commands")
            checks = lane.get("check_commands")
            if isinstance(declared_commands, list) and declared_commands:
                commands = [
                    ["sh", "-c", str(command)] for command in declared_commands
                ]
            elif (
                lane.get("lane_intent") == "verification-only"
                and isinstance(checks, list)
                and checks
            ):
                commands = [["sh", "-c", str(check)] for check in checks]
            else:
                commands = [
                    _default_team_lane_command(str(lane["lane_id"]), region)
                ]
            spec = {
                "lane_id": lane["lane_id"],
                "region": region,
                "commands": commands,
            }
            _attach_role_capability_team_fields(spec, lane)
            if lane.get("timeout_seconds") is not None:
                spec["timeout_seconds"] = lane["timeout_seconds"]
            specs.append(spec)
            continue
        spec = {
            "lane_id": lane["lane_id"],
            "adapter": adapter,
            "tier": lane["tier"],
            "region": region,
            "allowed_touched_files": region,
            "prompt": lane["prompt"],
            "budget": dict(lane["budget"]),
            "codex_binary": args.codex_binary,
            "claude_binary": args.claude_binary,
            "agy_binary": args.agy_binary,
            "gemini_binary": args.gemini_binary,
            "opencode_binary": args.opencode_binary,
        }
        _attach_role_capability_team_fields(spec, lane)
        if lane.get("timeout_seconds") is not None:
            spec["timeout_seconds"] = lane["timeout_seconds"]
        if lane.get("model") is not None:
            spec["model"] = lane["model"]
        specs.append(spec)
    return specs


def _attach_role_capability_team_fields(spec: dict, lane: dict) -> None:
    from witnessd.orro_workflow import (
        ERR_ORRO_ROLE_LANE_INTENT_INVALID,
        VALID_LANE_INTENTS,
        OrroWorkflowError,
    )

    lane_intent = lane.get("lane_intent")
    if lane_intent is not None:
        if (
            not isinstance(lane_intent, str)
            or lane_intent not in VALID_LANE_INTENTS
        ):
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_INTENT_INVALID,
                "role-lane lane_intent is invalid",
            )
        spec["lane_intent"] = lane_intent
    role_capability = lane.get("role_capability")
    if isinstance(role_capability, dict):
        spec["role_id"] = lane.get("role_id")
        spec["role_capability"] = role_capability.get("capability")
    if isinstance(lane.get("granted_write_scope"), list):
        spec["write_scope"] = list(lane["granted_write_scope"])
    if isinstance(lane.get("granted_tools"), dict):
        granted_tools = lane["granted_tools"]
        spec["tools"] = {
            "mcp": list(granted_tools.get("mcp", [])),
            "allow": list(granted_tools.get("allow", [])),
        }
    if isinstance(lane.get("granted_skill_routing"), dict):
        spec["skill_routing"] = dict(lane["granted_skill_routing"])


def _cmd_run_adapter(args: argparse.Namespace) -> int:
    if not args.command:
        _emit_orro_error(
            args,
            code="ERR_NO_PROMPT",
            message="proofrun adapter execution requires a worker prompt",
            reason="the adapter needs a worker prompt",
            required_input_or_grant=(
                "a prompt after `--` (or derive it from the sealed plan)"
            ),
            next_command=_adapter_proofrun_next_command(args, prompt=None),
        )
        return 2
    if not args.runner_sandbox:
        _emit_orro_error(
            args,
            code="ERR_WITNESSD_RUNNER_SANDBOX_REQUIRED",
            message="proofrun adapter execution requires --runner-sandbox <dir>",
            reason=RUNNER_SANDBOX_DIRECTORY_REASON,
            required_input_or_grant="--runner-sandbox DIR",
            next_command=_adapter_proofrun_next_command(
                args,
                prompt=list(args.command),
            ),
        )
        return 2

    from witnessd.adapter_run import LaneBlocked, run_adapter_lane
    from witnessd.adapters.codex import CodexAdapterError

    try:
        result = run_adapter_lane(
            root=os.path.abspath(args.root),
            sandbox=os.path.abspath(args.runner_sandbox),
            adapter=args.adapter,
            task_id=args.task_id,
            prompt=" ".join(args.command),
            arm=args.arm,
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
            allowed_touched_files=list(args.allow or []),
            capture_profile=args.capture_profile,
            signing_profile=_requested_signing_profile(args),
            keyless_options=_keyless_options(args),
        )
    except LaneBlocked as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    except CodexAdapterError as exc:
        print(exc.code, file=sys.stderr)
        return 1

    pending = 1
    print(
        f"{pending} adapter lane(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"evidence_dir: {result['evidence_dir']}")
    print(f"runner_kind: {result['runner_receipt']['runner_kind']}")
    return 0


def _adapter_proofrun_next_command(
    args: argparse.Namespace,
    *,
    prompt: list[str] | None,
) -> str:
    return _proofrun_runner_sandbox_next_command(
        args,
        adapter=str(args.adapter),
        prompt=prompt,
    )


def _proofrun_runner_sandbox_next_command(
    args: argparse.Namespace,
    *,
    adapter: str,
    prompt: list[str] | None,
) -> str:
    prompt_text = shlex.join(prompt) if prompt else '"<prompt>"'
    return (
        "PROJECT=/abs/project\n"
        'RUN_DIR="$PROJECT/.witnessd/runs/<run-id>"; '
        'SANDBOX="$RUN_DIR/sandbox"; mkdir -p "$SANDBOX"\n'
        "orro proofrun"
        ' --repo "$PROJECT" --home "$PROJECT/.witnessd" '
        f"--adapter {shlex.quote(adapter)} "
        '--runner-sandbox "$SANDBOX" -- '
        f"{prompt_text}\n"
        "hint: for shell-only evidence capture without agentic execution, "
        "consider `flowplan --profile verification-only --check ...`"
    )
