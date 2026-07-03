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
import json
import os
import shutil
import shlex
import sys
import time
from pathlib import Path

from witnessd.observer import ObserverSeparationError, assert_separated
from witnessd.status import render_status


def _cmd_run(args: argparse.Namespace) -> int:
    if args.adapter != "shell":
        return _cmd_run_adapter(args)

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
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.dirname(out_path)
    keys_dir = args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys")
    keys_dir = os.path.abspath(keys_dir)
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)

    allowed_touched_files = list(args.allow or [])
    commands = [list(args.command)]
    lane_result = run_shell_lane(sandbox=sandbox, commands=commands)

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
        runner_sandbox=sandbox,
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


def _cmd_run_adapter(args: argparse.Namespace) -> int:
    if not args.command:
        print("ERR_NO_PROMPT", file=sys.stderr)
        return 2

    from witnessd.adapter_run import LaneBlocked, run_adapter_lane

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
        )
    except LaneBlocked as exc:
        print(exc.reason, file=sys.stderr)
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


def _cmd_pilot_canary(args: argparse.Namespace) -> int:
    from witnessd.pilot import emit_canary_bundle

    bundle_path = emit_canary_bundle(keys_dir=args.keys_dir, out_dir=args.out)
    print(f"canary_bundle: {bundle_path}")
    return 0


def _cmd_pilot_archive_evidence(args: argparse.Namespace) -> int:
    from witnessd.pilot import record_archive_evidence

    artifacts: dict[str, str] = {}
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
    from witnessd.planner import (
        PlannerError,
        parse_draft_packets,
        plan_heuristic,
        seal_plan,
    )

    draft_events: list[dict] = []
    packets: list[dict] | None = None
    root = os.path.abspath(args.root)

    if args.draft_adapter:
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
                evidence_dir=args.draft_out,
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
        except (LaneBlocked, PlannerError, OSError) as exc:
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
    print(
        json.dumps(
            {"sealed_plan": sealed, "draft_events": draft_events},
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
    from witnessd.runlog import verify_runlog

    result = verify_runlog(_read_runlog(args.runlog))
    if result["ok"]:
        print("runlog: ok")
        return 0
    print(f"runlog: broken_at={result['broken_at']}", file=sys.stderr)
    return 1


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
    from witnessd.eventlog import EventLog
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
    from witnessd.eventlog import EventLog
    from witnessd.killswitch import active_targets_from_runlog, kill_all
    from witnessd.runlog import verify_runlog
    from witnessd.supervisor import WorkerSupervisor

    log = EventLog(args.runlog)
    records = log.read()
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
    state_root = _team_run_state_root(args, out_dir_path)
    if state_root is not None and _paths_overlap(Path(state_root), out_dir_path):
        print("ERR_TEAM_RUN_STATE_ROOT_INSIDE_OUTPUT", file=sys.stderr)
        return 2
    if state_root is not None and any(spec.get("adapter") == "codex" for spec in lane_specs):
        _seed_codex_auth(Path(state_root), args.codex_auth_source)

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

    parsed = {
        "lane_id": lane_id,
        "adapter": adapter,
        "tier": fields.get("tier", "agentic"),
        "region": [
            item.strip() for item in fields.get("region", "").split(",") if item.strip()
        ],
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

    run = sub.add_parser("run", help="observe a lane and emit signed evidence")
    run.add_argument(
        "--adapter",
        default="shell",
        choices=["shell", "codex", "claude", "opencode"],
    )
    run.add_argument("--root", default=".")
    run.add_argument("--runner-sandbox", required=True)
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
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=_cmd_run)

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

    plan = sub.add_parser("plan", help="emit a sealed W11 plan")
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
    plan.set_defaults(func=_cmd_plan)

    status = sub.add_parser("status", help="render evidence-pending status")
    status.add_argument("--evidence-dir", default=".")
    status.add_argument("--runlog", default=None)
    status.set_defaults(func=_cmd_status)

    verify = sub.add_parser("verify", help="verify runlog integrity")
    verify.add_argument("--runlog", required=True)
    verify.set_defaults(func=_cmd_verify)

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
    team_plan_run.set_defaults(func=_cmd_team_plan_run)

    team_ledger = sub.add_parser("team-ledger", help="verify a team ledger")
    team_ledger.add_argument("--ledger", required=True)
    team_ledger.add_argument("--json", action="store_true")
    team_ledger.set_defaults(func=_cmd_team_ledger)

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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it so command is the argv.
    if getattr(args, "command", None) and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
