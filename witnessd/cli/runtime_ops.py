from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from witnessd.__main__ import _count_pending, _derive_runlog_liveness
from witnessd.cli._output import _emit_orro_error, _read_runlog
from witnessd.status import render_status


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


def _cmd_verify(args: argparse.Namespace) -> int:
    if getattr(args, "run_dir", None):
        from witnessd.distribution import ProvisionError, run_depone_team_ledger

        run_dir = Path(args.run_dir).resolve(strict=False)
        home = Path(
            args.home or os.environ.get("WITNESSD_HOME") or run_dir.parent.parent
        ).resolve(strict=False)
        ledger_path = run_dir / "team-ledger.json"
        verdict_path = run_dir / "team-ledger-verdict.json"
        from witnessd.trust_anchor import resolve_trust_anchor

        trust_anchor = resolve_trust_anchor(home=home)
        try:
            verdict = run_depone_team_ledger(
                home=home,
                ledger_path=ledger_path,
                verdict_path=verdict_path,
                trusted_observer_public_key_file=trust_anchor.public_key_path,
            )
        except ProvisionError as exc:
            print(exc.code, file=sys.stderr)
            return 2
        payload = {
            "decision": verdict["decision"],
            "team_ledger": str(ledger_path),
            "team_ledger_verdict": str(verdict_path),
            "trust_anchor": trust_anchor.trust_anchor,
            "independent_trust_anchor": trust_anchor.independent,
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

        if not args.runner_sandbox:
            _emit_orro_error(
                args,
                code="ERR_WITNESSD_RUNNER_SANDBOX_REQUIRED",
                message="faultkit adapter execution requires --runner-sandbox <dir>",
                reason=(
                    "the codex/claude runner executes inside an isolated sandbox dir"
                ),
                required_input_or_grant="--runner-sandbox <dir>",
                next_command=(
                    "python3 -m witnessd faultkit budget-blowout "
                    "--root <repo> --runner-sandbox <dir>"
                ),
            )
            return 2

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
