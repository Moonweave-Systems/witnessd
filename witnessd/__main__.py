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
import os
import sys

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
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.dirname(out_path)
    keys_dir = args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys")
    keys_dir = os.path.abspath(keys_dir)
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)

    allowed_touched_files = list(args.allow or [])
    commands = [list(args.command)]
    lane_result = run_shell_lane(sandbox=sandbox, commands=commands)

    fixture = {
        "kind": "witnessd-lane-fixture",
        "adapter": args.adapter,
        "task_id": args.task_id,
        "commands": commands,
    }

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
    evidence_dir = os.path.abspath(args.evidence_dir)
    pending = _count_pending(evidence_dir)
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    return 0


def _cmd_self_test(args: argparse.Namespace) -> int:
    from witnessd import emitter, signing, substrate

    checks = [
        ("signing", signing._self_test),
        ("substrate", substrate._self_test),
        ("emitter", emitter._self_test),
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
    status.set_defaults(func=_cmd_status)

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
