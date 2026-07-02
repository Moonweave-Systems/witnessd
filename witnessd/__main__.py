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
import sys
import time

from witnessd.observer import ObserverSeparationError, assert_separated
from witnessd.status import render_status


def _cmd_run(args: argparse.Namespace) -> int:
    sandbox = os.path.abspath(args.runner_sandbox)
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

    if args.adapter != "shell":
        print(f"ERR_UNKNOWN_ADAPTER: {args.adapter}", file=sys.stderr)
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
    print(f"ERR_UNKNOWN_FAULT: {args.fault}", file=sys.stderr)
    return 2


def _cmd_self_test(args: argparse.Namespace) -> int:
    from witnessd import (
        emitter,
        faultkit,
        isolation,
        liveness,
        scheduler,
        session,
        signing,
        substrate,
        supervisor,
    )

    checks = [
        ("signing", signing._self_test),
        ("substrate", substrate._self_test),
        ("emitter", emitter._self_test),
        ("liveness", liveness._self_test),
        ("supervisor", supervisor._self_test),
        ("scheduler", scheduler._self_test),
        ("session", session._self_test),
        ("isolation", isolation._self_test),
        ("faultkit", faultkit._self_test),
    ]
    passed = 0
    for name, check in checks:
        try:
            check()
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
    run.add_argument("--adapter", default="shell", choices=["shell"])
    run.add_argument("--runner-sandbox", required=True)
    run.add_argument(
        "--out", required=True, help="observer output path (outside sandbox)"
    )
    run.add_argument("--log", required=True, help="observer log path (outside sandbox)")
    run.add_argument("--keys-dir", default=None)
    run.add_argument("--task-id", default="witnessd-lane")
    run.add_argument(
        "--allow", action="append", default=[], help="allowed touched file"
    )
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="render evidence-pending status")
    status.add_argument("--evidence-dir", default=".")
    status.add_argument("--runlog", default=None)
    status.set_defaults(func=_cmd_status)

    verify = sub.add_parser("verify", help="verify runlog integrity")
    verify.add_argument("--runlog", required=True)
    verify.set_defaults(func=_cmd_verify)

    doctor = sub.add_parser("doctor", help="report runlog-derived lane health")
    doctor.add_argument("--runlog", required=True)
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

    self_test = sub.add_parser("self-test", help="run module self-tests")
    self_test.add_argument("--all", action="store_true")
    self_test.set_defaults(func=_cmd_self_test)

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
