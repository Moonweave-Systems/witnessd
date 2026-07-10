"""witnessd CLI — `run` / `status` / `self-test`.

`run` wires the lane pipeline: observer/runner separation check (Task 4) →
shell adapter (Task 5) → observer_capture (Task 6) → Evidence Emitter (Task 11).
Separation is enforced fail-closed BEFORE any capture happens, so a --out/--log
inside the runner sandbox aborts with ERR_OBSERVER_NOT_SEPARATED and writes
nothing.

`status` renders only through the enum-gated render_status (Task 3): witnessd
never self-declares success; a lane is "evidence-pending" until a separate
Depone verification returns a verdict.

`self-test --all` runs each module's `_self_test` and reports `N/N passed`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import shutil
import shlex
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

from witnessd.observer import ObserverSeparationError, assert_separated
from witnessd.status import render_status


def _cmd_run(args: argparse.Namespace) -> int:
    if getattr(args, "goal", None):
        return _cmd_run_goal(args)

    if args.adapter != "shell":
        return _cmd_run_adapter(args)

    if not args.runner_sandbox:
        print("ERR_WITNESSD_RUN_GOAL_OR_SANDBOX_REQUIRED", file=sys.stderr)
        return 2

    sandbox = os.path.abspath(args.runner_sandbox)
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
    from witnessd.emitter import emit_lane_evidence
    from witnessd.fixture import (
        build_reference_adapter_fixture,
        build_shell_invocation,
    )
    from witnessd.privacy import (
        CAPTURE_PROFILE_REDACTED,
        build_redaction_context,
        redact_value,
    )
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.dirname(out_path)
    keys_dir = args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys")
    keys_dir = os.path.abspath(keys_dir)
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)

    allowed_touched_files = list(args.allow or [])
    commands = [list(args.command)]
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
        allowed_touched_files = list(redact_value(allowed_touched_files, redaction_context))

    # The source fixture is the declared (A0) side; Depone requires a proper
    # agent-fabric-reference-adapter-fixture, not a placeholder.
    fixture = build_reference_adapter_fixture(build_shell_invocation(args.task_id))

    result = emit_lane_evidence(
        lane_result,
        evidence_dir,
        private_key_path,
        fixture=fixture,
        allowed_touched_files=allowed_touched_files,
        public_key_path=public_key_path,
        task_id=args.task_id,
        runner_sandbox=str(redact_value(sandbox, redaction_context)),
        capture_profile=args.capture_profile,
        redaction_manifest=(
            redaction_context["manifest"] if redaction_context is not None else None
        ),
    )

    pending = 1
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"evidence_dir: {evidence_dir}")
    print(f"assurance (candidate, unverified): {result['assurance']}")
    print(f"trusted-observer public key (out-of-band): {result['public_key_path']}")
    return 0


def _cmd_run_goal(args: argparse.Namespace) -> int:
    from witnessd.distribution import (
        ProvisionError,
        run_depone_team_ledger,
        validate_depone_pin,
    )
    from witnessd.eventlog import EventLog
    from witnessd.fanin import run_team
    from witnessd.orro_workflow import (
        OrroWorkflowError,
        assert_workflow_phase_allowed,
        load_workflow_plan,
        load_role_lane_plan,
        write_workflow_plan_binding,
        write_role_lane_plan_binding,
        write_workflow_role_dispatch,
    )
    from witnessd.planner import dispatch, seal_plan
    from witnessd.signing import gen_operator_keypair

    repo = Path(args.repo or ".").resolve(strict=False)
    home = Path(
        args.home
        or os.environ.get("WITNESSD_HOME")
        or (repo / ".witnessd")
    ).resolve(strict=False)
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
            workflow_plan = load_workflow_plan(workflow_plan_source, expected_goal=args.goal)
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
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

    if workflow_plan is not None:
        try:
            assert_workflow_phase_allowed(workflow_plan, "proofrun")
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 2

    if args.run_dir:
        out_dir = Path(args.run_dir).resolve(strict=False)
    else:
        out_dir = home / "runs" / f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.monotonic_ns()}"
    out_dir.mkdir(parents=True, exist_ok=True)

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

    packets = (
        _role_lane_plan_packets(role_lane_plan)
        if role_lane_plan is not None
        else _default_w18_packets(args.goal)
    )
    sealed = seal_plan(packets, goal=args.goal)
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
    private_key_path, public_key_path = gen_operator_keypair(str(keys_dir))
    lane_specs = (
        _role_lane_plan_team_specs(role_lane_plan, args)
        if role_lane_plan is not None
        else [_lane_packet_to_run_team_spec(packet, args) for packet in sealed["packets"]]
    )
    run_team(
        lane_specs,
        repo_root=str(repo),
        out_dir=str(out_dir),
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        leader_objective=args.goal,
        stop_rule="evidence-pending",
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
    )
    verdict_path = out_dir / "team-ledger-verdict.json"
    try:
        verdict = run_depone_team_ledger(
            home=home,
            ledger_path=out_dir / "team-ledger.json",
            verdict_path=verdict_path,
        )
    except ProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    payload = {
        "decision": verdict["decision"],
        "lane_count": verdict["lane_count"],
        "run_dir": str(out_dir),
        "sealed_plan": str(sealed_path),
        "team_ledger": str(out_dir / "team-ledger.json"),
        "team_ledger_verdict": str(verdict_path),
    }
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
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        adapter = str(lane["adapter"])
        region = list(lane["region"])
        if adapter == "shell":
            specs.append(
                {
                    "lane_id": lane["lane_id"],
                    "region": region,
                    "commands": [
                        _default_team_lane_command(str(lane["lane_id"]), region)
                    ],
                }
            )
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
            "opencode_binary": args.opencode_binary,
        }
        specs.append(spec)
    return specs


def _cmd_run_adapter(args: argparse.Namespace) -> int:
    if not args.command:
        print("ERR_NO_PROMPT", file=sys.stderr)
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
            opencode_binary=args.opencode_binary,
            allowed_touched_files=list(args.allow or []),
            capture_profile=args.capture_profile,
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


def _count_pending(evidence_dir: str) -> int:
    if not os.path.isdir(evidence_dir):
        return 0
    count = 0
    for root, _dirs, files in os.walk(evidence_dir):
        count += sum(1 for name in files if name == "capture-manifest.json")
    return count


def _cmd_status(args: argparse.Namespace) -> int:
    if args.runlog:
        states = _derive_runlog_liveness(args.runlog)
        for lane_id in sorted(states):
            print(f"lane {lane_id}: {states[lane_id]}")
        return 0
    evidence_dir = os.path.abspath(args.evidence_dir)
    pending = _count_pending(evidence_dir)
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    return 0


def _cmd_pilot_init(args: argparse.Namespace) -> int:
    from witnessd.pilot import write_deployment_record

    deployed_runtime = bool(args.deployed_runtime and args.not_dogfood and args.not_ci)
    record_path = write_deployment_record(
        operator=args.operator,
        team_scope=args.team_scope,
        out_dir=args.out,
        deployed_runtime=deployed_runtime,
        local_dogfood=not deployed_runtime,
        ci_only=not deployed_runtime,
        repo_root=args.deployment_root,
    )
    print(f"deployment_record: {record_path}")
    return 0


def _cmd_pilot_close(args: argparse.Namespace) -> int:
    from witnessd.pilot import close_deployment_record

    digest = close_deployment_record(args.record)
    print(f"deployment_record_sha256: {digest}")
    return 0


def _cmd_pilot_rotation_record(args: argparse.Namespace) -> int:
    from witnessd.pilot import write_rotation_record

    record_path = write_rotation_record(
        archive_path=args.archive,
        out_dir=args.out,
        retired_key_id=args.retired_key_id,
    )
    print(f"rotation_record: {record_path}")
    return 0


def _cmd_pilot_canary(args: argparse.Namespace) -> int:
    from witnessd.pilot import emit_canary_bundle

    bundle_path = emit_canary_bundle(keys_dir=args.keys_dir, out_dir=args.out)
    print(f"canary_bundle: {bundle_path}")
    return 0


def _cmd_pilot_archive_evidence(args: argparse.Namespace) -> int:
    from witnessd.pilot import record_archive_evidence

    artifacts: dict[str, str | Path] = {}
    for entry in args.artifact:
        if "=" not in entry:
            print("ERR_ARCHIVE_ARTIFACT_FORMAT", file=sys.stderr)
            return 2
        evidence_id, path = entry.split("=", 1)
        if not evidence_id or not path:
            print("ERR_ARCHIVE_ARTIFACT_FORMAT", file=sys.stderr)
            return 2
        artifacts[evidence_id] = path
    try:
        archive_path = record_archive_evidence(
            archive_path=args.archive,
            artifacts=artifacts,
            out_path=args.out,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"operator_key_archive: {archive_path}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    from witnessd.adapter_run import LaneBlocked, run_adapter_lane
    from witnessd.adapters.codex import CodexAdapterError
    from witnessd.orro_workflow import (
        OrroWorkflowError,
        compile_role_lane_plan,
        compile_workflow_plan,
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
            workflow_plan = compile_workflow_plan(goal=args.goal, profile=args.profile)
        except OrroWorkflowError as exc:
            _emit_orro_error(
                args,
                code=exc.code,
                message="unknown ORRO workflow profile",
            )
            return 2

    if args.draft_adapter:
        draft_root = f"{root.rstrip(os.sep)}-witnessd-plan-draft"
        draft_out = (
            args.draft_out
            or os.path.join(draft_root, "evidence")
        )
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
            workflow_plan = compile_workflow_plan(goal=args.goal, profile="code-change")
            payload["workflow_plan"] = workflow_plan
        try:
            role_lane_plan = compile_role_lane_plan(
                workflow_plan=workflow_plan,
                lane_adapter=args.lane_adapter,
            )
            role_lane_plan_ref = write_role_lane_plan(
                Path(args.role_lanes_out).resolve(strict=False),
                role_lane_plan,
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(args, code=exc.code, message=str(exc))
            return 1
        payload["role_lane_plan"] = role_lane_plan_ref
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


def _read_runlog(path: str) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _derive_runlog_liveness(path: str) -> dict[str, str]:
    from witnessd.liveness import derive_liveness

    records = _read_runlog(path)
    return derive_liveness(records, now_monotonic=time.monotonic())


def _cmd_verify(args: argparse.Namespace) -> int:
    if getattr(args, "run_dir", None):
        from witnessd.distribution import ProvisionError, run_depone_team_ledger

        run_dir = Path(args.run_dir).resolve(strict=False)
        home = Path(
            args.home
            or os.environ.get("WITNESSD_HOME")
            or run_dir.parent.parent
        ).resolve(strict=False)
        ledger_path = run_dir / "team-ledger.json"
        verdict_path = run_dir / "team-ledger-verdict.json"
        try:
            verdict = run_depone_team_ledger(
                home=home, ledger_path=ledger_path, verdict_path=verdict_path
            )
        except ProvisionError as exc:
            print(exc.code, file=sys.stderr)
            return 2
        payload = {
            "decision": verdict["decision"],
            "team_ledger": str(ledger_path),
            "team_ledger_verdict": str(verdict_path),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if verdict["decision"] == "pass" else 1
    if not args.runlog:
        print("ERR_VERIFY_RUN_DIR_OR_RUNLOG_REQUIRED", file=sys.stderr)
        return 2
    from witnessd.runlog import verify_runlog

    result = verify_runlog(_read_runlog(args.runlog))
    if result["ok"]:
        print("runlog: ok")
        return 0
    print(f"runlog: broken_at={result['broken_at']}", file=sys.stderr)
    return 1


def _depone_subprocess_env(home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if home is None:
        return env
    from witnessd.distribution import validate_depone_pin

    provision = validate_depone_pin(home)
    depone_root = Path(str(provision["depone"]["root"])).resolve(strict=False)
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(depone_root)
        if not current_pythonpath
        else f"{depone_root}{os.pathsep}{current_pythonpath}"
    )
    return env


def _run_depone_json(command: list[str], *, env: dict[str, str]) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "depone", *command, "--json"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if not completed.stdout.strip():
        return completed.returncode, {
            "error": {
                "code": "ERR_ORRO_DEPONE_DELEGATION_FAILED",
                "message": completed.stderr.strip()
                or "Depone verifier produced no JSON output",
            }
        }
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:
        return completed.returncode, {
            "error": {
                "code": "ERR_ORRO_DEPONE_DELEGATION_INVALID_JSON",
                "message": completed.stdout,
            }
        }


def _emit_orro_error(args: argparse.Namespace, *, code: str, message: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"error": {"code": code, "message": message}}, sort_keys=True))
        return
    print(code, file=sys.stderr)


def _emit_orro_engine_lock_check_error(
    args: argparse.Namespace, *, code: str, message: str
) -> None:
    payload = {
        "command": "orro engine-lock check",
        "locked": False,
        "mismatches": [],
        "boundary": {
            "approves_merge": False,
            "raises_assurance": False,
            "executes_commands": False,
            "verifies_evidence": False,
        },
        "error": {"code": code, "message": message},
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return
    print(code, file=sys.stderr)


def _collect_orro_artifact_hashes(
    evidence_dir: Path, *, out_path: Path | None = None
) -> list[dict[str, str]]:
    generated_names = {
        "orro-handoff.json",
        "proofcheck-verdict.json",
        "team-ledger-verdict.json",
    }
    artifact_hashes = []
    for path in sorted(p for p in evidence_dir.rglob("*") if p.is_file()):
        if path.name in generated_names or (out_path is not None and path == out_path):
            continue
        artifact_hashes.append(
            {
                "path": str(path.relative_to(evidence_dir)),
                "sha256": _hash_file(path),
            }
        )
    return artifact_hashes


def _proofcheck_binding(
    evidence_dir: Path, *, out_path: Path | None = None
) -> dict[str, object]:
    return {
        "kind": "orro-proofcheck-binding",
        "schema_version": "1.0",
        "evidence_dir": str(evidence_dir),
        "artifact_hashes": _collect_orro_artifact_hashes(evidence_dir, out_path=out_path),
    }


def _cmd_proofcheck(args: argparse.Namespace) -> int:
    from witnessd.orro_workflow import (
        role_lane_plan_binding_ref,
        workflow_plan_binding_ref,
        workflow_role_dispatch_ref,
    )

    evidence_arg = args.evidence_dir_option or args.evidence_dir
    if not evidence_arg:
        _emit_orro_error(
            args,
            code="ERR_ORRO_PROOFCHECK_INPUT_REQUIRED",
            message="evidence directory is required",
        )
        return 2
    evidence_dir = Path(evidence_arg).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    try:
        env = _depone_subprocess_env(home)
    except Exception as exc:  # noqa: BLE001 - surface pin/readiness failure as usage
        _emit_orro_error(
            args,
            code=str(exc),
            message="Depone verifier readiness is blocked",
        )
        return 2

    out_path = Path(args.out).resolve(strict=False) if args.out else None
    command = ["proofcheck", "--evidence-dir", str(evidence_dir)]
    if out_path is not None:
        command.extend(["--out", str(out_path)])
    code, payload = _run_depone_json(command, env=env)
    binding: dict[str, object] | None = None
    binding_error: str | None = None
    if code == 0 and payload.get("decision") == "pass":
        binding = _proofcheck_binding(evidence_dir, out_path=out_path)
    workflow_plan_ref = workflow_plan_binding_ref(evidence_dir)
    role_lane_plan_ref = role_lane_plan_binding_ref(evidence_dir)
    workflow_role_dispatch = workflow_role_dispatch_ref(evidence_dir)
    if code == 0 and payload.get("decision") == "pass" and out_path is not None:
        try:
            verdict_payload = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            verdict_payload = None
            binding_error = str(exc)
        if isinstance(verdict_payload, dict):
            verdict_payload["orro_binding"] = binding
            if workflow_plan_ref is not None:
                verdict_payload["workflow_plan"] = workflow_plan_ref
            if role_lane_plan_ref is not None:
                verdict_payload["role_lane_plan"] = role_lane_plan_ref
            if workflow_role_dispatch is not None:
                verdict_payload["workflow_role_dispatch"] = workflow_role_dispatch
            try:
                out_path.write_text(
                    json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                binding_error = str(exc)
            else:
                binding_error = None
        elif binding_error is None:
            binding_error = "proofcheck-verdict.json must be a JSON object"
    if binding_error is not None:
        payload = {
            "decision": "blocked",
            "verifier_command": payload.get("verifier_command", "proofcheck"),
            "error": {
                "code": "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
                "message": binding_error,
            },
        }
        code = 1
    result = {
        "command": "proofcheck",
        "verifier_command": payload.get("verifier_command", "proofcheck"),
        "decision": payload.get("decision", "blocked"),
        "evidence_dir": str(evidence_dir),
        **({"orro_binding": binding} if binding is not None and binding_error is None else {}),
        **({"workflow_plan": workflow_plan_ref} if workflow_plan_ref is not None else {}),
        **({"role_lane_plan": role_lane_plan_ref} if role_lane_plan_ref is not None else {}),
        **({"workflow_role_dispatch": workflow_role_dispatch} if workflow_role_dispatch is not None else {}),
        "error_count": payload.get("error_count", 1 if payload.get("error") else 0),
        **({"out": payload["out"]} if payload.get("out") else {}),
        **({"errors": payload["errors"]} if payload.get("errors") else {}),
        **({"error": payload["error"]} if payload.get("error") else {}),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if code == 0 and result["decision"] == "pass" else 1


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cmd_handoff(args: argparse.Namespace) -> int:
    from witnessd.orro_workflow import (
        role_lane_plan_binding_ref,
        workflow_plan_binding_ref,
        workflow_role_dispatch_ref,
    )

    evidence_arg = args.evidence_dir_option or args.evidence_dir
    if not evidence_arg:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_INPUT_REQUIRED",
            message="evidence directory is required",
        )
        return 2
    evidence_dir = Path(evidence_arg).resolve(strict=False)
    if not evidence_dir.is_dir():
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_EVIDENCE_DIR_MISSING",
            message=f"evidence directory is missing: {evidence_dir}",
        )
        return 2

    proofcheck_path = evidence_dir / "proofcheck-verdict.json"
    if not proofcheck_path.is_file():
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_REQUIRED",
            message="proofcheck-verdict.json is required before handoff",
        )
        return 1
    try:
        proofcheck_payload = json.loads(proofcheck_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            message=f"failed to read proofcheck-verdict.json: {exc}",
        )
        return 1
    if not isinstance(proofcheck_payload, dict):
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            message="proofcheck-verdict.json must be a JSON object",
        )
        return 1
    if proofcheck_payload.get("decision") != "pass":
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_NOT_PASS",
            message="proofcheck-verdict.json decision must be pass",
        )
        return 1
    out_path = Path(args.out).resolve(strict=False) if args.out else None
    expected_binding = _proofcheck_binding(evidence_dir, out_path=out_path)
    proofcheck_binding = proofcheck_payload.get("orro_binding")
    if not isinstance(proofcheck_binding, dict):
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_UNBOUND",
            message="proofcheck-verdict.json must include an ORRO proofcheck binding",
        )
        return 1
    if proofcheck_binding != expected_binding:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_BINDING_MISMATCH",
            message="proofcheck-verdict.json does not match this evidence directory",
        )
        return 1

    artifact_hashes = _collect_orro_artifact_hashes(evidence_dir, out_path=out_path)
    workflow_plan_ref = workflow_plan_binding_ref(evidence_dir)
    role_lane_plan_ref = role_lane_plan_binding_ref(evidence_dir)
    workflow_role_dispatch = workflow_role_dispatch_ref(evidence_dir)
    decision_refs = []
    for name in ("proofcheck-verdict.json", "team-ledger-verdict.json"):
        path = evidence_dir / name
        if not path.is_file():
            continue
        ref = {"path": name, "sha256": _hash_file(path)}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if isinstance(payload.get("decision"), str):
            ref["decision"] = payload["decision"]
        decision_refs.append(ref)

    payload = {
        "kind": "orro-handoff",
        "schema_version": "1.0",
        "evidence_dir": str(evidence_dir),
        "artifact_hashes": artifact_hashes,
        "decision_refs": decision_refs,
        **({"workflow_plan": workflow_plan_ref} if workflow_plan_ref is not None else {}),
        **({"role_lane_plan": role_lane_plan_ref} if role_lane_plan_ref is not None else {}),
        **({"workflow_role_dispatch": workflow_role_dispatch} if workflow_role_dispatch is not None else {}),
        "boundary": {
            "approves_merge": False,
            "raises_assurance": False,
        },
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


def _cmd_orro_doctor(args: argparse.Namespace) -> int:
    checks = []
    checks.append({"name": "witnessd_import", "status": "pass"})
    home = Path(args.home).resolve(strict=False) if args.home else None
    env = os.environ.copy()
    if home is not None:
        try:
            env = _depone_subprocess_env(home)
        except Exception as exc:  # noqa: BLE001 - readiness check reports pin failure
            checks.append(
                {
                    "name": "depone_pin",
                    "status": "blocked",
                    "code": str(exc),
                    "path": str(home / "provision.json"),
                }
            )
        else:
            checks.append(
                {
                    "name": "depone_pin",
                    "status": "pass",
                    "path": str(home / "provision.json"),
                }
            )
    if args.engine_lock:
        if home is None:
            checks.append(
                {
                    "name": "engine_lock",
                    "status": "blocked",
                    "locked": False,
                    "code": "ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                }
            )
        else:
            from witnessd.distribution import ProvisionError, check_orro_engine_lock

            try:
                engine_lock = check_orro_engine_lock(
                    home=home,
                    witnessd_root=Path(__file__).resolve().parents[1],
                    lock_path=Path(args.engine_lock).resolve(strict=False),
                )
            except ProvisionError as exc:
                checks.append(
                    {
                        "name": "engine_lock",
                        "status": "blocked",
                        "locked": False,
                        "code": exc.code,
                    }
                )
            else:
                checks.append(
                    {
                        "name": "engine_lock",
                        "status": "pass" if engine_lock["locked"] else "blocked",
                        "locked": engine_lock["locked"],
                        "code": engine_lock.get("error", {}).get("code"),
                        "mismatches": engine_lock["mismatches"],
                    }
                )
    completed = subprocess.run(
        [sys.executable, "-m", "depone", "doctor", "--self-test"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    checks.append(
        {
            "name": "depone_doctor",
            "status": "pass" if completed.returncode == 0 else "blocked",
            "detail": completed.stdout.strip() or completed.stderr.strip(),
        }
    )

    for adapter in args.adapter or ["codex", "claude", "opencode"]:
        checks.append(
            {
                "name": f"adapter:{adapter}",
                "status": "pass" if shutil.which(adapter) else "missing",
            }
        )
    decision = (
        "blocked" if any(check["status"] == "blocked" for check in checks) else "pass"
    )
    payload = {
        "command": "orro doctor",
        "decision": decision,
        "checks": checks,
        "boundary": {
            "verifier_refuted": False,
            "executes_recipes": False,
            "raises_assurance": False,
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if decision == "pass" else 1


def _cmd_orro_engine_lock(args: argparse.Namespace) -> int:
    if not args.home:
        if args.check:
            _emit_orro_engine_lock_check_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                message="--home is required to check the pinned Depone provision",
            )
        else:
            _emit_orro_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                message="--home is required to read the pinned Depone provision",
            )
        return 2
    from witnessd.distribution import (
        ERR_ORRO_ENGINE_LOCK_MISMATCH,
        ProvisionError,
        build_orro_engine_lock,
        check_orro_engine_lock,
    )

    if args.check:
        try:
            check_payload = check_orro_engine_lock(
                home=Path(args.home).resolve(strict=False),
                witnessd_root=Path(__file__).resolve().parents[1],
                lock_path=Path(args.check).resolve(strict=False),
            )
        except ProvisionError as exc:
            _emit_orro_engine_lock_check_error(
                args,
                code=exc.code,
                message="ORRO engine lock cannot be checked against the current provision",
            )
            return 2
        print(json.dumps(check_payload, sort_keys=True))
        if check_payload["locked"]:
            return 0
        if check_payload.get("error", {}).get("code") == ERR_ORRO_ENGINE_LOCK_MISMATCH:
            return 1
        return 2
    try:
        payload = build_orro_engine_lock(
            home=Path(args.home).resolve(strict=False),
            witnessd_root=Path(__file__).resolve().parents[1],
        )
    except ProvisionError as exc:
        _emit_orro_error(
            args,
            code=exc.code,
            message="ORRO engine lock cannot be produced from the current provision",
        )
        return 2
    if args.out:
        out_path = Path(args.out).resolve(strict=False)
        try:
            out_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _emit_orro_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_WRITE_FAILED",
                message=str(exc),
            )
            return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


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

    mode_count = sum(bool(mode) for mode in (args.dry_run, args.once, args.until_complete))
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
                reasons = list(payload_reasons) if isinstance(payload_reasons, list) else []
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
            if len(steps) >= max_steps and decision_final in {"needs-proofcheck", "ready-for-handoff"}:
                error = {
                    "code": "ERR_ORRO_AUTO_MAX_STEPS_REACHED",
                    "message": "orro auto --until-complete stopped before complete because --max-steps was reached",
                }
                reasons = [*reasons, "max steps reached before completion"]
            else:
                maybe_error = current_payload.get("error")
                error = maybe_error if isinstance(maybe_error, dict) else {
                    "code": "ERR_ORRO_AUTO_BLOCKED",
                    "message": "ORRO auto until-complete is blocked by continuation state",
                }
                payload_reasons = current_payload.get("reasons", reasons)
                reasons = list(payload_reasons) if isinstance(payload_reasons, list) else reasons
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
            error=payload.get("error") if isinstance(payload.get("error"), dict) else None,
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
    return child_code, receipt, after_code if before_code == 0 or child_code == 0 else before_code, after_payload


def _cmd_doctor(args: argparse.Namespace) -> int:
    if args.external_worktree:
        from witnessd.state import detect_state_contention

        errors = detect_state_contention(
            witnessd_worktree=os.path.abspath(args.root),
            external_active_worktrees=[
                os.path.abspath(path) for path in args.external_worktree
            ],
        )
        for error in errors:
            print(error, file=sys.stderr)
        return 3 if errors else 0
    if not args.runlog:
        return 0
    states = _derive_runlog_liveness(args.runlog)
    bad = {lane_id: state for lane_id, state in states.items() if state != "active"}
    for lane_id in sorted(states):
        print(f"lane {lane_id}: {states[lane_id]}")
    return 1 if bad else 0


def _cmd_isolation(args: argparse.Namespace) -> int:
    if args.self_test:
        from witnessd.isolation import isolation_self_test

        isolation_self_test()
        return 0
    print("ERR_ISOLATION_COMMAND_REQUIRED", file=sys.stderr)
    return 2


def _cmd_faultkit(args: argparse.Namespace) -> int:
    if args.fault == "budget-blowout":
        from witnessd.adapter_run import LaneBlocked, run_adapter_lane

        try:
            run_adapter_lane(
                root=os.path.abspath(args.root),
                sandbox=os.path.abspath(args.runner_sandbox),
                adapter="codex",
                task_id=args.task_id,
                prompt=args.prompt,
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={
                    "max_tokens": args.max_tokens,
                    "max_usd": args.max_usd,
                    "max_depth": args.max_depth,
                },
                predicted_tokens=args.max_tokens + 1,
                predicted_usd=0.0,
                codex_binary=args.codex_binary,
            )
        except LaneBlocked as exc:
            print(exc.reason)
            return 1 if exc.reason == "budget_exceeded" else 2
        print("budget_blowout_not_reproduced", file=sys.stderr)
        return 2
    if args.fault == "zombie-hang":
        from witnessd.faultkit import zombie_hang

        zombie_hang(args.runlog)
        print(f"faultkit zombie-hang: {args.runlog}")
        return 0
    if args.fault == "crash-mid-toolcall":
        from witnessd.faultkit import crash_mid_toolcall

        state = crash_mid_toolcall(
            runlog_before_path=args.runlog_before,
            runlog_after_path=args.runlog_after,
            session_path=args.session,
        )
        print(
            "faultkit crash-mid-toolcall: "
            f"{state['run_state']} cursor={state['tool_call_cursor']} "
            f"reapplied={state['idempotency_reapplied']}"
        )
        return 0
    if args.fault == "pause-race":
        from witnessd.eventlog import EventLog
        from witnessd.faultkit import pause_race

        log = EventLog(args.runlog)
        pause_race(log, run_id=args.run_id)
        print(f"faultkit pause-race: {args.runlog}")
        return 0
    print(f"ERR_UNKNOWN_FAULT: {args.fault}", file=sys.stderr)
    return 2


def _cmd_pause(args: argparse.Namespace) -> int:
    from witnessd.eventlog import EventLog, EventLogIntegrityError
    from witnessd.pause import PauseError, append_user_pause

    try:
        append_user_pause(EventLog(args.runlog), args.run_id, source="cli")
    except PauseError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print(render_status(pending=1, verdict=None))
    return 0


def _cmd_resume_pause(args: argparse.Namespace) -> int:
    from witnessd.eventlog import EventLog
    from witnessd.pause import PauseError, append_user_resume

    try:
        append_user_resume(EventLog(args.runlog), args.run_id, confirm=args.confirm)
    except PauseError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print(render_status(pending=1, verdict=None))
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    if not args.all:
        print("ERR_KILL_SCOPE_REQUIRED", file=sys.stderr)
        return 2
    from witnessd.eventlog import EventLog, EventLogIntegrityError
    from witnessd.killswitch import active_targets_from_runlog, kill_all
    from witnessd.runlog import verify_runlog
    from witnessd.supervisor import WorkerSupervisor

    try:
        log = EventLog(args.runlog)
        records = log.read()
    except EventLogIntegrityError as exc:
        print(f"runlog: broken_at={exc.broken_at}", file=sys.stderr)
        return 1
    verification = verify_runlog(records)
    if not verification["ok"]:
        print(f"runlog: broken_at={verification['broken_at']}", file=sys.stderr)
        return 1
    supervisor = WorkerSupervisor(log, run_id=args.run_id)
    result = kill_all(
        supervisor,
        log,
        args.run_id,
        targets=active_targets_from_runlog(records),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["all_confirmed_dead"] else 1


def _cmd_learn(args: argparse.Namespace) -> int:
    if args.learn_cmd != "promote":
        print("ERR_LEARN_COMMAND_REQUIRED", file=sys.stderr)
        return 2
    from witnessd.eventlog import EventLog
    from witnessd.learning import promote_learning_delta

    with open(args.delta, encoding="utf-8") as handle:
        delta = json.load(handle)
    committed_captures = []
    for path in args.capture:
        with open(path, encoding="utf-8") as handle:
            committed_captures.append(json.load(handle))
    approval_events = []
    for path in args.approval_log:
        approval_events.extend(_read_runlog(path))
    result = promote_learning_delta(
        delta,
        log=EventLog(args.runlog),
        run_id=args.run_id,
        priv=args.private_key,
        pub=args.public_key,
        committed_captures=committed_captures,
        approval_events=approval_events,
        evidence_dir=args.evidence_dir,
    )
    print(
        json.dumps({k: v for k, v in result.items() if k != "bundle"}, sort_keys=True)
    )
    if not result["promoted"]:
        return 1
    bundle_path = args.bundle_out
    if bundle_path:
        with open(bundle_path, "w", encoding="utf-8") as handle:
            json.dump(result["bundle"], handle, sort_keys=True, indent=2)
            handle.write("\n")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from witnessd.installer import InstallerError, atomic_install, atomic_upgrade
    from witnessd.pause import PauseError, assert_not_paused
    from witnessd.state import StateNamespace

    try:
        from witnessd.eventlog import EventLog

        runlog = args.runlog or StateNamespace(args.root).runlog_path
        assert_not_paused(EventLog(runlog).read())
        if args.cmd == "install":
            result = atomic_install(
                payload_path=args.payload,
                dest_dir=args.dest,
                config_path=args.config,
                shim_dir=args.shim_dir,
                version=args.version,
            )
        else:
            result = atomic_upgrade(
                payload_path=args.payload,
                dest_dir=args.dest,
                config_path=args.config,
                shim_dir=args.shim_dir,
                version=args.version,
            )
    except (InstallerError, PauseError) as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from witnessd.distribution import InitConfig, ProvisionError, init_witnessd_home

    home = Path(
        args.home
        or os.environ.get("WITNESSD_HOME")
        or (Path(args.repo).resolve(strict=False) / ".witnessd")
    )
    depone_root = Path(args.depone_root).expanduser() if args.depone_root else None
    try:
        result = init_witnessd_home(
            InitConfig(
                home=home,
                witnessd_root=Path(__file__).resolve().parents[1],
                depone_root=depone_root,
                network_allowed=args.allow_network,
                depone_repository=args.depone_repository,
                depone_ref=args.depone_ref,
            )
        )
    except ProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def _cmd_scout(args: argparse.Namespace) -> int:
    from witnessd.superflow import run_scout

    home = Path(
        args.home
        or os.environ.get("WITNESSD_HOME")
        or (Path(args.repo).resolve(strict=False) / ".witnessd")
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    result = run_scout(args.goal, repo=Path(args.repo), home=home, out_dir=out_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    from witnessd.eventlog import EventLog
    from witnessd.router import RouteExhaustedError, route_model

    root = Path(args.root).resolve()
    runlog_path = (
        Path(args.runlog) if args.runlog else root / ".witnessd" / "route-runlog.jsonl"
    )
    runlog_path.parent.mkdir(parents=True, exist_ok=True)
    unsupported = set(args.unsupported_model or [])
    log = EventLog(str(runlog_path))
    try:
        decision = route_model(
            task_id=args.task_id,
            tier=args.tier,
            log=log,
            is_supported=lambda model: model not in unsupported,
        )
    except RouteExhaustedError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print(json.dumps(decision, sort_keys=True))
    return 0


def _cmd_team_run(args: argparse.Namespace) -> int:
    from witnessd.fanin import run_team
    from witnessd.signing import gen_operator_keypair

    out_dir_path = Path(args.out).resolve()
    out_dir = str(out_dir_path)
    lane_specs = [_parse_team_lane(text) for text in args.lane]
    try:
        merge_groups = [_parse_team_merge_group(text) for text in args.merge_group]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        _apply_lane_prompt_files(lane_specs, args.lane_prompt_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    state_root = _team_run_state_root(args, out_dir_path)
    if state_root is not None and _paths_overlap(Path(state_root), out_dir_path):
        print("ERR_TEAM_RUN_STATE_ROOT_INSIDE_OUTPUT", file=sys.stderr)
        return 2
    codex_specs = [spec for spec in lane_specs if spec.get("adapter") == "codex"]
    if len(codex_specs) > 1 and state_root is None and not _codex_specs_are_isolated(codex_specs):
        print("ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED", file=sys.stderr)
        return 2
    if state_root is not None and codex_specs:
        if len(codex_specs) > 1:
            for spec in codex_specs:
                lane_state_root = _team_run_lane_state_root(
                    Path(state_root), str(spec["lane_id"])
                )
                spec["state_root"] = str(lane_state_root)
                _seed_codex_auth(lane_state_root, args.codex_auth_source)
        else:
            _seed_codex_auth(Path(state_root), args.codex_auth_source)
    elif len(codex_specs) > 1:
        for spec in codex_specs:
            _seed_codex_auth(Path(str(spec["state_root"])), args.codex_auth_source)

    keys_dir = os.path.abspath(args.keys_dir or (out_dir.rstrip(os.sep) + "-keys"))
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)
    result = run_team(
        lane_specs,
        repo_root=args.repo,
        out_dir=out_dir,
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        state_root=state_root,
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
        merge_groups=merge_groups,
    )
    pending = len(result["ledger"]["lanes"])
    print(
        f"{pending} team lane(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"team_ledger: {os.path.join(out_dir, 'team-ledger.json')}")
    return 0


def _team_run_state_root(args: argparse.Namespace, out_dir: Path) -> str | None:
    if args.state_root is not None:
        return str(Path(args.state_root).resolve(strict=False))
    if args.codex_auth_source:
        return str(
            Path(str(out_dir).rstrip(os.sep) + "-w4-state-root").resolve(strict=False)
        )
    return None


def _team_run_lane_state_root(state_root: Path, lane_id: str) -> Path:
    slug = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-" for char in lane_id
    ).strip("-._")
    digest = hashlib.sha256(lane_id.encode("utf-8")).hexdigest()[:16]
    return state_root / f"{slug or 'lane'}-{digest}"


def _codex_specs_are_isolated(codex_specs: list[dict]) -> bool:
    roots: list[Path] = []
    for spec in codex_specs:
        state_root = spec.get("state_root")
        if not state_root:
            return False
        roots.append(Path(str(state_root)).resolve(strict=False))
    if len({str(root) for root in roots}) != len(roots):
        return False
    for left_index, left in enumerate(roots):
        for right in roots[left_index + 1 :]:
            if _paths_overlap(left, right):
                return False
    return True


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


def _parse_team_merge_group(text: str) -> dict:
    lane_id, sep, rest = text.partition(":")
    if sep != ":" or not lane_id.strip():
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    sources_text, sep, files_text = rest.partition(":")
    if sep != ":":
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    sources = [part.strip() for part in sources_text.split(",") if part.strip()]
    files = [part.strip() for part in files_text.split(",") if part.strip()]
    if len(sources) < 2 or not files:
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    return {"lane_id": lane_id.strip(), "sources": sources, "files": files}


def _cmd_team_plan_run(args: argparse.Namespace) -> int:
    from witnessd.eventlog import EventLog
    from witnessd.fanin import run_team
    from witnessd.planner import dispatch, plan_heuristic, seal_plan
    from witnessd.signing import gen_operator_keypair

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.draft_adapter != "heuristic":
        print("ERR_PLAN_RUN_DRAFT_ADAPTER_UNSUPPORTED", file=sys.stderr)
        return 2

    state_root = _team_plan_state_root(args, out_dir)
    if state_root is not None and _paths_overlap(Path(state_root), out_dir):
        print("ERR_PLAN_RUN_STATE_ROOT_INSIDE_OUTPUT", file=sys.stderr)
        return 2

    budget = {
        "max_tokens": args.max_tokens,
        "max_usd": args.max_usd,
        "max_depth": args.max_depth,
    }
    sealed = seal_plan(
        plan_heuristic(
            args.goal,
            seed=args.seed,
            root=args.repo,
            adapter=args.lane_adapter,
            budget=budget,
            tier=args.tier,
        ),
        goal=args.goal,
    )
    sealed_path = out_dir / "sealed-plan.json"
    sealed_path.write_text(
        json.dumps(sealed, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    dispatch_log = EventLog(str(out_dir / "dispatch-log.jsonl"))
    for event in dispatch(sealed):
        dispatch_log.append(event)

    keys_dir = Path(args.keys_dir or (str(out_dir).rstrip(os.sep) + "-keys")).resolve()
    keys_dir.mkdir(parents=True, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(str(keys_dir))
    lane_specs = [
        _lane_packet_to_run_team_spec(packet, args) for packet in sealed["packets"]
    ]
    if args.lane_adapter == "codex" and state_root is not None:
        _seed_codex_auth(Path(state_root), args.codex_auth_source)
    result = run_team(
        lane_specs,
        repo_root=args.repo,
        out_dir=str(out_dir),
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        leader_objective=args.goal,
        stop_rule="evidence-pending",
        state_root=state_root,
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
    )
    pending = len(result["ledger"]["lanes"])
    print(
        f"{pending} planned team lane(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"sealed_plan: {sealed_path}")
    print(f"dispatch_log: {out_dir / 'dispatch-log.jsonl'}")
    print(f"team_ledger: {out_dir / 'team-ledger.json'}")
    return 0


def _team_plan_state_root(args: argparse.Namespace, out_dir: Path) -> str | None:
    if args.state_root is not None:
        return str(Path(args.state_root).resolve(strict=False))
    if args.lane_adapter == "shell":
        return None
    return str(
        Path(str(out_dir).rstrip(os.sep) + "-w4-state-root").resolve(strict=False)
    )


def _is_inside_or_equal(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_inside_or_equal(left, right) or _is_inside_or_equal(right, left)


def _seed_codex_auth(state_root: Path, source: str | None) -> None:
    if not source:
        return
    source_path = Path(source).expanduser().resolve(strict=False)
    if not source_path.exists():
        raise RuntimeError(f"codex auth source does not exist: {source_path}")
    target = state_root.resolve(strict=False) / ".witnessd" / "codex-home" / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    target.chmod(0o600)


def _cmd_a2_observer_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("ERR_NO_COMMAND", file=sys.stderr)
        return 2

    from witnessd.a2 import run_observer_launched_shell_lane, uid_for_user
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.abspath(args.out)
    observer_dir = os.path.abspath(args.observer_dir)
    keys_dir = os.path.abspath(args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys"))
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)
    runner_uid = args.runner_uid
    if runner_uid is None:
        runner_uid = uid_for_user(args.runner_user)

    result = run_observer_launched_shell_lane(
        sandbox=os.path.abspath(args.runner_sandbox),
        commands=[list(args.command)],
        evidence_dir=evidence_dir,
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        observer_dir=observer_dir,
        runner_user=args.runner_user,
        runner_uid=runner_uid,
        allowed_touched_files=list(args.allow or []),
        task_id=args.task_id,
        test_command=["sh", "-c", args.test_command] if args.test_command else None,
    )

    pending = 1
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"evidence_dir: {evidence_dir}")
    print(f"assurance (candidate, unverified): {result['assurance']}")
    print(f"trusted-observer public key (out-of-band): {result['public_key_path']}")
    return 0


def _lane_packet_to_run_team_spec(
    packet: dict, args: argparse.Namespace | None = None
) -> dict:
    if packet["adapter"] == "shell":
        return {
            "lane_id": packet["lane_id"],
            "region": list(packet["region"]),
            "commands": [
                _default_team_lane_command(packet["lane_id"], packet["region"])
            ],
        }
    spec = {
        "lane_id": packet["lane_id"],
        "adapter": packet["adapter"],
        "tier": packet["tier"],
        "region": list(packet["region"]),
        "prompt": packet["prompt"],
        "budget": dict(packet["budget"]),
    }
    if args is not None:
        spec.update(
            {
                "codex_binary": args.codex_binary,
                "claude_binary": args.claude_binary,
                "opencode_binary": args.opencode_binary,
            }
        )
    return spec


def _cmd_team_ledger(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    pending = (
        len(ledger.get("lanes", [])) if isinstance(ledger.get("lanes"), list) else 0
    )
    status = render_status(pending=pending, verdict=None)
    result = {
        "decision": status,
        "pending": pending,
        "ledger": str(ledger_path),
        "message": f"{pending} team lane(s) pending Depone verification",
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['message']} ({status})")
    return 0


def _cmd_lane_exec(args: argparse.Namespace) -> int:
    from witnessd.fanin import run_lane_exec_from_spec

    return run_lane_exec_from_spec(args.spec_json, args.result_json)


def _cmd_team_resume_audit(args: argparse.Namespace) -> int:
    from witnessd.fanin import resume_audit

    audit = resume_audit(args.out, run_id=args.run_id)
    if args.json:
        print(json.dumps(audit, sort_keys=True))
    else:
        print(f"team_resume_audit: {Path(args.out).resolve(strict=False) / 'team-resume-audit.json'}")
    return 0


def _cmd_team_resume(args: argparse.Namespace) -> int:
    from witnessd.fanin import resume_team

    try:
        result = resume_team(
            args.run_dir,
            run_id=args.run_id,
            max_parallel=args.max_parallel,
            fail_fast=args.fail_fast,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ledger": str(result["base_dir"] / "team-ledger.json")}, sort_keys=True))
    else:
        print(f"team_resume: {result['base_dir'] / 'team-ledger.json'}")
    return 0


def _cmd_team_kill(args: argparse.Namespace) -> int:
    runlog = args.runlog
    if runlog is None and args.state_root is not None:
        state_root = Path(args.state_root).resolve(strict=False)
        manifest_path = state_root / "team-run.json"
        if not manifest_path.is_file():
            print("ERR_TEAM_KILL_STATE_MANIFEST_MISSING", file=sys.stderr)
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "witnessd-team-run-state":
            print("ERR_TEAM_KILL_STATE_MANIFEST_INVALID", file=sys.stderr)
            return 2
        manifest_runlog = manifest.get("runlog")
        if not isinstance(manifest_runlog, str) or not manifest_runlog:
            print("ERR_TEAM_KILL_STATE_MANIFEST_INVALID", file=sys.stderr)
            return 2
        runlog = manifest_runlog
    if runlog is None:
        print("ERR_TEAM_KILL_RUNLOG_REQUIRED", file=sys.stderr)
        return 2
    args.runlog = runlog
    return _cmd_kill(args)


def _parse_team_lane(text: str) -> dict:
    lane_id, sep, body = text.partition(":")
    lane_id = lane_id.strip()
    if not lane_id or sep != ":":
        raise ValueError("ERR_TEAM_LANE_FORMAT")

    parts = body.split(":")
    keyed = any(part.startswith("adapter=") for part in parts)
    if not keyed:
        region = [item.strip() for item in body.split(",") if item.strip()]
        return {
            "lane_id": lane_id,
            "region": region,
            "commands": [_default_team_lane_command(lane_id, region)],
        }

    fields: dict[str, str] = {}
    for part in parts:
        key, field_sep, value = part.partition("=")
        if field_sep != "=" or not key.strip():
            raise ValueError("ERR_TEAM_LANE_FORMAT")
        fields[key.strip()] = value.strip()

    from witnessd.adapters.base import RUNNER_KIND_BY_ADAPTER

    adapter = fields.get("adapter", "")
    valid_adapters = set(RUNNER_KIND_BY_ADAPTER) | {"shell"}
    if adapter not in valid_adapters:
        raise ValueError("ERR_TEAM_LANE_ADAPTER")
    prompt = fields.get("prompt", "")
    if not prompt:
        raise ValueError("ERR_TEAM_LANE_PROMPT")

    region = [
        item.strip() for item in fields.get("region", "").split(",") if item.strip()
    ]
    parsed = {
        "lane_id": lane_id,
        "adapter": adapter,
        "tier": fields.get("tier", "agentic"),
        "region": region,
        "allowed_touched_files": list(region),
        "prompt": prompt,
    }
    return parsed


def _default_team_lane_command(lane_id: str, region: list[str]) -> list[str]:
    statements: list[str] = []
    for path in region:
        parent = os.path.dirname(path)
        if parent:
            statements.append(f"mkdir -p {shlex.quote(parent)}")
        statements.append(
            f"printf '%s\\n' {shlex.quote(lane_id)} > {shlex.quote(path)}"
        )
    return ["sh", "-c", " && ".join(statements) if statements else "true"]


def _cmd_self_test(args: argparse.Namespace) -> int:
    from witnessd import (
        budget,
        emitter,
        fanin,
        faultkit,
        installer,
        isolation,
        killswitch,
        learning,
        lock,
        liveness,
        pause,
        pilot,
        preflight,
        router,
        scheduler,
        session,
        signing,
        state,
        substrate,
        supervisor,
        team_ledger,
        worktree,
    )
    from witnessd.adapters import base as adapter_base
    from witnessd.adapters import codex as codex_adapter

    checks = [
        ("signing", signing._self_test),
        ("substrate", substrate._self_test),
        ("emitter", emitter._self_test),
        ("liveness", liveness._self_test),
        ("supervisor", supervisor._self_test),
        ("scheduler", scheduler._self_test),
        ("session", session._self_test),
        ("isolation", isolation._self_test),
        ("pause", pause._self_test),
        ("killswitch", killswitch._self_test),
        ("pilot", pilot._self_test),
        ("learning", learning._self_test),
        ("installer", installer._self_test),
        ("faultkit", faultkit._self_test),
        ("lock", lock._self_test),
        ("worktree", worktree._self_test),
        ("team_ledger", team_ledger._self_test),
        ("fanin", fanin._self_test),
        ("adapter_base", adapter_base._self_test),
        ("codex_adapter", codex_adapter._self_test),
        ("preflight", preflight._self_test),
        ("router", router._self_test),
        ("budget", budget._self_test),
        ("state", state._self_test),
    ]
    report_pass_names = {
        "adapter_base",
        "codex_adapter",
        "preflight",
        "router",
        "budget",
        "state",
        "pause",
        "killswitch",
        "learning",
        "installer",
    }
    passed = 0
    for name, check in checks:
        try:
            check()
            if name in report_pass_names:
                print(f"witnessd {name} --self-test: pass")
            passed += 1
        except Exception as exc:  # noqa: BLE001 — report which self-test failed
            print(f"witnessd {name} --self-test: FAIL ({exc})", file=sys.stderr)
    total = len(checks)
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="witnessd")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="initialize witnessd config and pinned Depone")
    init.add_argument("--home", default=None)
    init.add_argument("--repo", default=".")
    init.add_argument("--depone-root", default=None)
    init.add_argument("--depone-repository", default=None)
    init.add_argument("--depone-ref", default=None)
    init.add_argument(
        "--allow-network",
        action="store_true",
        help="allow setup-time network provisioning when no local Depone root is supplied",
    )
    init.set_defaults(func=_cmd_init)

    scout = sub.add_parser("scout", help="run read-only ORRO repo scout")
    scout.add_argument("goal")
    scout.add_argument("--repo", default=".")
    scout.add_argument("--home", default=None)
    scout.add_argument("--out-dir", default=None)
    scout.set_defaults(func=_cmd_scout)

    run = sub.add_parser("run", help="observe a lane and emit signed evidence")
    _add_run_args(run)
    run.set_defaults(func=_cmd_run)

    proofrun = sub.add_parser(
        "proofrun",
        help="ORRO evidence-backed execution alias; emits evidence without final trust",
    )
    _add_run_args(proofrun)
    proofrun.set_defaults(func=_cmd_run)

    a2 = sub.add_parser(
        "a2-observer-run",
        help="run one observer-launched shell lane for W12 real A2 evidence",
    )
    a2.add_argument("--runner-sandbox", required=True)
    a2.add_argument("--out", required=True, help="observer evidence directory")
    a2.add_argument("--observer-dir", required=True)
    a2.add_argument("--keys-dir", default=None)
    a2.add_argument("--runner-user", default="ubuntu")
    a2.add_argument("--runner-uid", type=int, default=None)
    a2.add_argument("--task-id", default="w12-real-a2")
    a2.add_argument("--test-command", default=None)
    a2.add_argument("--allow", action="append", default=[])
    a2.add_argument("command", nargs=argparse.REMAINDER)
    a2.set_defaults(func=_cmd_a2_observer_run)

    plan = sub.add_parser(
        "plan",
        help="compatibility name for flowplan; emits a sealed plan without execution",
    )
    _add_plan_args(plan)
    plan.set_defaults(func=_cmd_plan)

    flowplan = sub.add_parser(
        "flowplan",
        help="ORRO plan-only workflow design; emits a sealed plan without execution",
    )
    _add_flowplan_args(flowplan)
    flowplan.set_defaults(func=_cmd_plan)

    status = sub.add_parser("status", help="render evidence-pending status")
    status.add_argument("--evidence-dir", default=".")
    status.add_argument("--runlog", default=None)
    status.set_defaults(func=_cmd_status)

    verify = sub.add_parser("verify", help="verify a run directory or runlog integrity")
    verify.add_argument("run_dir", nargs="?")
    verify.add_argument("--home", default=None)
    verify.add_argument("--runlog", default=None)
    verify.set_defaults(func=_cmd_verify)

    proofcheck = sub.add_parser(
        "proofcheck",
        help="ORRO offline proof verification wrapper delegated to Depone",
    )
    proofcheck.add_argument("evidence_dir", nargs="?")
    proofcheck.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    proofcheck.add_argument("--home", default=None)
    proofcheck.add_argument("--out", default=None)
    proofcheck.add_argument("--json", action="store_true")
    proofcheck.set_defaults(func=_cmd_proofcheck)

    handoff = sub.add_parser(
        "handoff",
        help="package ORRO evidence hashes and verifier decision references",
    )
    handoff.add_argument("evidence_dir", nargs="?")
    handoff.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    handoff.add_argument("--out", default=None)
    handoff.add_argument("--json", action="store_true")
    handoff.set_defaults(func=_cmd_handoff)

    route = sub.add_parser("route", help="dry-run W4 model routing")
    route.add_argument("--root", default=".")
    route.add_argument("--runlog", default=None)
    route.add_argument("--task-id", default="witnessd-route")
    route.add_argument(
        "--tier", required=True, choices=["quick", "agentic", "frontier"]
    )
    route.add_argument("--unsupported-model", action="append", default=[])
    route.set_defaults(func=_cmd_route)

    doctor = sub.add_parser("doctor", help="report runlog-derived lane health")
    doctor.add_argument("--runlog", default=None)
    doctor.add_argument("--root", default=".")
    doctor.add_argument("--external-worktree", action="append", default=[])
    doctor.set_defaults(func=_cmd_doctor)

    orro_doctor = sub.add_parser("orro-doctor", help=argparse.SUPPRESS)
    orro_doctor.add_argument("--home", default=None)
    orro_doctor.add_argument(
        "--adapter",
        action="append",
        default=None,
        choices=["codex", "claude", "opencode"],
    )
    orro_doctor.add_argument("--json", action="store_true")
    orro_doctor.add_argument("--engine-lock", default=None)
    orro_doctor.set_defaults(func=_cmd_orro_doctor)

    engine_lock = sub.add_parser(
        "engine-lock",
        help="write/check ORRO distribution metadata for pinned engine commits",
    )
    engine_lock.add_argument("--home", default=None)
    engine_lock.add_argument("--out", default=None)
    engine_lock.add_argument("--check", default=None)
    engine_lock.add_argument("--json", action="store_true")
    engine_lock.set_defaults(func=_cmd_orro_engine_lock)

    orro_next = sub.add_parser("orro-next", help=argparse.SUPPRESS)
    orro_next.add_argument("run_dir", nargs="?")
    orro_next.add_argument("--home", default=None)
    orro_next.add_argument("--out", default=None)
    orro_next.add_argument("--json", action="store_true")
    orro_next.set_defaults(func=_cmd_orro_next)

    orro_advise = sub.add_parser("orro-advise", help=argparse.SUPPRESS)
    orro_advise.add_argument("goal", nargs="?")
    orro_advise.add_argument("--repo", default=".")
    orro_advise.add_argument("--home", default=None)
    orro_advise.add_argument("--out", default=None)
    orro_advise.add_argument("--json", action="store_true")
    orro_advise.set_defaults(func=_cmd_orro_advise)

    orro_report = sub.add_parser("orro-report", help=argparse.SUPPRESS)
    orro_report.add_argument("run_dir", nargs="?")
    orro_report.add_argument("--home", default=None)
    orro_report.add_argument("--out", default=None)
    orro_report.add_argument("--workstyle-decision", default=None)
    orro_report.add_argument("--json", action="store_true")
    orro_report.set_defaults(func=_cmd_orro_report)

    orro_auto = sub.add_parser("orro-auto", help=argparse.SUPPRESS)
    orro_auto.add_argument("run_dir", nargs="?")
    orro_auto.add_argument("--dry-run", action="store_true")
    orro_auto.add_argument("--once", action="store_true")
    orro_auto.add_argument("--until-complete", action="store_true")
    orro_auto.add_argument("--max-steps", type=int, default=None)
    orro_auto.add_argument("--home", default=None)
    orro_auto.add_argument("--out", default=None)
    orro_auto.add_argument("--json", action="store_true")
    orro_auto.set_defaults(func=_cmd_orro_auto)

    isolation = sub.add_parser("isolation", help="isolation contract checks")
    isolation.add_argument("--self-test", action="store_true")
    isolation.set_defaults(func=_cmd_isolation)

    faultkit = sub.add_parser("faultkit", help="deterministic fault injection")
    faultkit_sub = faultkit.add_subparsers(dest="fault", required=True)
    zombie = faultkit_sub.add_parser("zombie-hang")
    zombie.add_argument("--runlog", required=True)
    zombie.set_defaults(func=_cmd_faultkit)
    crash = faultkit_sub.add_parser("crash-mid-toolcall")
    crash.add_argument("--runlog-before", required=True)
    crash.add_argument("--runlog-after", required=True)
    crash.add_argument("--session", required=True)
    crash.set_defaults(func=_cmd_faultkit)
    pause_race = faultkit_sub.add_parser("pause-race")
    pause_race.add_argument("--runlog", required=True)
    pause_race.add_argument("--run-id", default="faultkit-pause-run")
    pause_race.set_defaults(func=_cmd_faultkit)
    budget = faultkit_sub.add_parser("budget-blowout")
    budget.add_argument("--root", required=True)
    budget.add_argument("--runner-sandbox", required=True)
    budget.add_argument("--codex-binary", default="codex")
    budget.add_argument("--task-id", default="budget-blowout")
    budget.add_argument("--prompt", default="trigger budget blowout")
    budget.add_argument("--max-tokens", type=int, default=1)
    budget.add_argument("--max-usd", type=float, default=10**9)
    budget.add_argument("--max-depth", type=int, default=3)
    budget.set_defaults(func=_cmd_faultkit)

    team = sub.add_parser("team", help="run a local team fan-in")
    team_sub = team.add_subparsers(dest="team_cmd", required=True)
    team_run = team_sub.add_parser("run", help="emit team fan-in evidence")
    team_run.add_argument("--repo", required=True)
    team_run.add_argument("--out", required=True)
    team_run.add_argument("--keys-dir", default=None)
    team_run.add_argument("--state-root", default=None)
    team_run.add_argument("--codex-auth-source", default=None)
    team_run.add_argument("--max-parallel", type=int, default=None)
    team_run.add_argument("--fail-fast", action="store_true")
    team_run.add_argument("--lane-prompt-file", action="append", default=[])
    team_run.add_argument(
        "--merge-group",
        action="append",
        default=[],
        help="merge_lane:source_a,source_b:file[,file...] for explicit overlapped-source merge evidence",
    )
    team_run.add_argument(
        "--lane",
        action="append",
        required=True,
        help="lane_id:file[,file...] or lane_id:adapter=codex:tier=quick:region=file[,file...]:prompt=...",
    )
    team_run.set_defaults(func=_cmd_team_run)

    team_plan_run = team_sub.add_parser(
        "plan-run", help="plan a goal and run the resulting team lanes"
    )
    team_plan_run.add_argument("goal")
    team_plan_run.add_argument("--repo", required=True)
    team_plan_run.add_argument("--out", required=True)
    team_plan_run.add_argument("--keys-dir", default=None)
    team_plan_run.add_argument("--seed", default="w11")
    team_plan_run.add_argument(
        "--draft-adapter",
        choices=["heuristic", "codex", "claude", "opencode"],
        default="heuristic",
    )
    team_plan_run.add_argument(
        "--lane-adapter",
        choices=["shell", "codex", "claude", "opencode"],
        default="shell",
    )
    team_plan_run.add_argument("--tier", default="agentic")
    team_plan_run.add_argument("--max-tokens", type=int, default=10**9)
    team_plan_run.add_argument("--max-usd", type=float, default=10**9)
    team_plan_run.add_argument("--max-depth", type=int, default=3)
    team_plan_run.add_argument("--state-root", default=None)
    team_plan_run.add_argument("--codex-auth-source", default="~/.codex/auth.json")
    team_plan_run.add_argument("--codex-binary", default="codex")
    team_plan_run.add_argument("--claude-binary", default="claude")
    team_plan_run.add_argument("--opencode-binary", default="opencode")
    team_plan_run.add_argument("--max-parallel", type=int, default=None)
    team_plan_run.add_argument("--fail-fast", action="store_true")
    team_plan_run.set_defaults(func=_cmd_team_plan_run)

    team_ledger = sub.add_parser("team-ledger", help="show team-ledger status pending Depone verification")
    team_ledger.add_argument("--ledger", required=True)
    team_ledger.add_argument("--json", action="store_true")
    team_ledger.set_defaults(func=_cmd_team_ledger)

    team_resume_audit = team_sub.add_parser(
        "resume-audit", help="audit surviving team lane bytes without replay"
    )
    team_resume_audit.add_argument("--out", required=True)
    team_resume_audit.add_argument("--run-id", default="w15-resume-audit")
    team_resume_audit.add_argument("--json", action="store_true")
    team_resume_audit.set_defaults(func=_cmd_team_resume_audit)

    team_resume = team_sub.add_parser("resume", help="resume an interrupted team run")
    team_resume.add_argument("run_dir")
    team_resume.add_argument("--run-id", default="w3-team")
    team_resume.add_argument("--max-parallel", type=int, default=None)
    team_resume.add_argument("--fail-fast", action="store_true")
    team_resume.add_argument("--json", action="store_true")
    team_resume.set_defaults(func=_cmd_team_resume)

    team_kill = team_sub.add_parser("kill", help="kill all live team lanes")
    team_kill.add_argument("--runlog", default=None)
    team_kill.add_argument("--state-root", default=None)
    team_kill.add_argument("--run-id", default="team-kill")
    team_kill.add_argument("--all", action="store_true", default=True)
    team_kill.set_defaults(func=_cmd_team_kill)

    lane_exec = sub.add_parser("lane-exec", help=argparse.SUPPRESS)
    lane_exec.add_argument("--spec-json", required=True)
    lane_exec.add_argument("--result-json", required=True)
    lane_exec.set_defaults(func=_cmd_lane_exec)

    pause = sub.add_parser("pause", help="append a user pause event")
    pause.add_argument("run_id")
    pause.add_argument("--runlog", required=True)
    pause.set_defaults(func=_cmd_pause)

    resume = sub.add_parser("resume", help="append an explicit user resume event")
    resume.add_argument("run_id")
    resume.add_argument("--runlog", required=True)
    resume.add_argument("--confirm", action="store_true")
    resume.set_defaults(func=_cmd_resume_pause)

    kill = sub.add_parser("kill", help="kill all supervised children")
    kill.add_argument("--all", action="store_true")
    kill.add_argument("--runlog", required=True)
    kill.add_argument("--run-id", default="witnessd-kill")
    kill.set_defaults(func=_cmd_kill)

    learn = sub.add_parser("learn", help="learning delta commands")
    learn_sub = learn.add_subparsers(dest="learn_cmd", required=True)
    promote = learn_sub.add_parser("promote")
    promote.add_argument("--delta", required=True)
    promote.add_argument("--capture", action="append", required=True)
    promote.add_argument("--approval-log", action="append", default=[])
    promote.add_argument("--runlog", required=True)
    promote.add_argument("--run-id", default="witnessd-learning")
    promote.add_argument("--private-key", required=True)
    promote.add_argument("--public-key", required=True)
    promote.add_argument("--evidence-dir", required=True)
    promote.add_argument("--bundle-out", default=None)
    promote.set_defaults(func=_cmd_learn)

    for name, help_text in (
        ("install", "atomically install witnessd payload"),
        ("upgrade", "atomically upgrade witnessd payload"),
    ):
        install = sub.add_parser(name, help=help_text)
        install.add_argument("--payload", required=True)
        install.add_argument("--dest", required=True)
        install.add_argument("--config", required=True)
        install.add_argument("--shim-dir", required=True)
        install.add_argument("--version", required=True)
        install.add_argument("--root", default=".")
        install.add_argument("--runlog", default=None)
        install.set_defaults(func=_cmd_install)

    self_test = sub.add_parser("self-test", help="run module self-tests")
    self_test.add_argument("--all", action="store_true")
    self_test.set_defaults(func=_cmd_self_test)

    pilot = sub.add_parser("pilot", help="external-team pilot tooling")
    pilot_sub = pilot.add_subparsers(dest="pilot_cmd", required=True)

    pilot_init = pilot_sub.add_parser("init", help="create a pilot deployment record")
    pilot_init.add_argument("--operator", required=True)
    pilot_init.add_argument("--team-scope", required=True)
    pilot_init.add_argument("--out", required=True)
    pilot_init.add_argument("--deployed-runtime", action="store_true")
    pilot_init.add_argument("--not-dogfood", action="store_true")
    pilot_init.add_argument("--not-ci", action="store_true")
    pilot_init.add_argument(
        "--deployment-root",
        default=None,
        help="repo path of the deployed runtime whose git SHA is recorded (default: this tree)",
    )
    pilot_init.set_defaults(func=_cmd_pilot_init)

    pilot_close = pilot_sub.add_parser("close", help="close a pilot deployment record")
    pilot_close.add_argument("--record", required=True)
    pilot_close.set_defaults(func=_cmd_pilot_close)

    pilot_rotation = pilot_sub.add_parser(
        "rotation-record", help="create an operator key rotation record"
    )
    pilot_rotation.add_argument("--archive", required=True)
    pilot_rotation.add_argument("--out", required=True)
    pilot_rotation.add_argument("--retired-key-id", default="witnessd-operator")
    pilot_rotation.set_defaults(func=_cmd_pilot_rotation_record)

    pilot_canary = pilot_sub.add_parser(
        "canary", help="emit a signed operator key-rotation canary bundle"
    )
    pilot_canary.add_argument("--keys-dir", required=True)
    pilot_canary.add_argument("--out", required=True)
    pilot_canary.set_defaults(func=_cmd_pilot_canary)

    pilot_archive = pilot_sub.add_parser(
        "archive-evidence", help="record pilot evidence paths and hashes"
    )
    pilot_archive.add_argument("--archive", required=True)
    pilot_archive.add_argument("--out", default=None)
    pilot_archive.add_argument("--artifact", action="append", required=True)
    pilot_archive.set_defaults(func=_cmd_pilot_archive_evidence)

    return parser


def _add_plan_args(plan: argparse.ArgumentParser) -> None:
    plan.add_argument("goal")
    plan.add_argument("--root", default=".")
    plan.add_argument("--seed", default="w11")
    plan.add_argument("--draft-adapter", choices=["codex", "claude", "opencode"])
    plan.add_argument("--draft-out", default=None)
    plan.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    plan.add_argument("--codex-binary", default="codex")
    plan.add_argument("--claude-binary", default="claude")
    plan.add_argument("--opencode-binary", default="opencode")
    plan.add_argument("--max-tokens", type=int, default=10**9)
    plan.add_argument("--max-usd", type=float, default=10**9)
    plan.add_argument("--max-depth", type=int, default=3)
    plan.add_argument("--predicted-tokens", type=int, default=0)
    plan.add_argument("--predicted-usd", type=float, default=0.0)


def _add_run_args(run: argparse.ArgumentParser) -> None:
    run.add_argument("--goal", default=None, help=argparse.SUPPRESS)
    run.add_argument("--repo", default=None)
    run.add_argument("--home", default=None)
    run.add_argument("--run-dir", default=None)
    run.add_argument("--workflow-plan", default=None)
    run.add_argument("--role-lane-plan", default=None)
    run.add_argument("--json", action="store_true")
    run.add_argument("--max-parallel", type=int, default=None)
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument(
        "--adapter",
        default="shell",
        choices=["shell", "codex", "claude", "opencode"],
    )
    run.add_argument("--root", default=".")
    run.add_argument("--runner-sandbox", default=None)
    run.add_argument(
        "--out", default=None, help="observer output path (outside sandbox)"
    )
    run.add_argument("--log", default=None, help="observer log path (outside sandbox)")
    run.add_argument("--keys-dir", default=None)
    run.add_argument("--task-id", default="witnessd-lane")
    run.add_argument("--arm", default="direct", choices=["direct", "governed"])
    run.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    run.add_argument("--codex-binary", default="codex")
    run.add_argument("--claude-binary", default="claude")
    run.add_argument("--opencode-binary", default="opencode")
    run.add_argument("--max-tokens", type=int, default=10**9)
    run.add_argument("--max-usd", type=float, default=10**9)
    run.add_argument("--max-depth", type=int, default=3)
    run.add_argument("--predicted-tokens", type=int, default=0)
    run.add_argument("--predicted-usd", type=float, default=0.0)
    run.add_argument(
        "--allow", action="append", default=[], help="allowed touched file"
    )
    run.add_argument(
        "--capture-profile",
        choices=["full", "redacted"],
        default="full",
    )
    run.add_argument("command", nargs=argparse.REMAINDER)


def _add_flowplan_args(flowplan: argparse.ArgumentParser) -> None:
    flowplan.add_argument("goal")
    flowplan.add_argument("--root", default=".")
    flowplan.add_argument("--seed", default="w11")
    flowplan.add_argument("--profile", default=None)
    flowplan.add_argument("--out", default=None)
    flowplan.add_argument("--role-lanes-out", default=None)
    flowplan.add_argument(
        "--lane-adapter",
        default="shell",
        choices=["shell", "codex", "claude", "opencode"],
    )
    flowplan.add_argument("--json", action="store_true")
    flowplan.set_defaults(
        draft_adapter=None,
        draft_out=None,
        tier="agentic",
        codex_binary="codex",
        claude_binary="claude",
        opencode_binary="opencode",
        max_tokens=10**9,
        max_usd=10**9,
        max_depth=3,
        predicted_tokens=0,
        predicted_usd=0.0,
    )


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_run_goal_argv(
        _normalize_orro_argv(
            _normalize_superflow_argv(list(sys.argv[1:] if argv is None else argv))
        )
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it so command is the argv.
    if getattr(args, "command", None) and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


def _normalize_run_goal_argv(argv: list[str]) -> list[str]:
    if (
        len(argv) < 2
        or argv[0] not in {"run", "proofrun"}
        or "--" in argv
        or "--goal" in argv
    ):
        return argv
    first = argv[1]
    if first.startswith("-"):
        return argv
    return [argv[0], "--goal", first, *argv[2:]]


def _normalize_superflow_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "superflow":
        return argv
    if len(argv) >= 2 and argv[1] == "scout":
        return ["scout", *argv[2:]]
    return argv


def _normalize_orro_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "orro":
        return argv
    if len(argv) >= 2 and argv[1] == "init":
        return ["init", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "scout":
        return ["scout", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "flowplan":
        return ["flowplan", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "proofrun":
        return ["proofrun", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "proofcheck":
        return ["proofcheck", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "handoff":
        return ["handoff", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "doctor":
        return ["orro-doctor", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "engine-lock":
        return ["engine-lock", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "lock":
        return ["engine-lock", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "next":
        return ["orro-next", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "advise":
        return ["orro-advise", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "report":
        return ["orro-report", *argv[2:]]
    if len(argv) >= 2 and argv[1] == "auto":
        return ["orro-auto", *argv[2:]]
    return argv


if __name__ == "__main__":
    sys.exit(main())
