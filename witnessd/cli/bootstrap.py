from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from witnessd import __file__
from witnessd.cli._output import _emit_orro_error

def _cmd_init(args: argparse.Namespace) -> int:
    from witnessd.distribution import InitConfig, ProvisionError, init_witnessd_home
    from witnessd.role_capability import RolepackError

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
                team_path=Path(args.team).expanduser() if args.team else None,
            )
        )
    except ProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except RolepackError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def _cmd_orro_setup(args: argparse.Namespace) -> int:
    from witnessd.distribution import (
        InitConfig,
        ProvisionError,
        build_orro_engine_lock,
        init_witnessd_home,
        validate_orro_setup_depone_pin,
    )

    home = Path(args.home or os.environ.get("WITNESSD_HOME") or ".witnessd")
    if not home.is_absolute():
        home = home.resolve(strict=False)
    depone_root = Path(args.depone_root).expanduser() if args.depone_root else None
    try:
        init_result = init_witnessd_home(
            InitConfig(
                home=home,
                witnessd_root=Path(__file__).resolve().parents[1],
                depone_root=depone_root,
                network_allowed=True,
                depone_repository=args.depone_repository,
                depone_ref=args.depone_ref,
            )
        )
        provision = validate_orro_setup_depone_pin(
            home=home,
            depone_ref=args.depone_ref,
        )
        engine_lock = build_orro_engine_lock(
            home=home,
            witnessd_root=Path(__file__).resolve().parents[1],
        )
        engine_lock_path = home / "orro-engine-lock.json"
        engine_lock_path.write_text(
            json.dumps(engine_lock, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ProvisionError as exc:
        _emit_orro_error(
            args,
            code=exc.code,
            message="ORRO setup could not provision a pinned Depone verifier",
        )
        return 2
    except OSError as exc:
        _emit_orro_error(
            args,
            code="ERR_ORRO_SETUP_WRITE_FAILED",
            message=str(exc),
        )
        return 1

    depone = provision["depone"]
    payload = {
        "kind": "orro-setup-result",
        "schema_version": "0.1",
        "command": "orro setup",
        "home": str(home),
        "config": init_result["config"],
        "provision": init_result["provision"],
        "keys_dir": init_result["keys_dir"],
        "depone_root": str(depone["root"]),
        "depone_commit": str(depone["commit"]),
        "depone_source": str(depone["source"]),
        "depone_network_used": bool(depone["network_used"]),
        "engine_lock": str(engine_lock_path),
        "engine_lock_commit": str(engine_lock["depone"]["commit"]),
        "next_steps": [
            f"python3 -m orro doctor --home {shlex.quote(str(home))} --json",
            "python3 -m orro team init --template developer --yes",
            f'python3 -m orro team go "<goal>" --repo <repo> --home {shlex.quote(str(home))} --json',
        ],
        "boundary": {
            "setup_may_use_network": True,
            "runtime_may_use_network": False,
            "verify_may_use_network": False,
            "verifies_evidence": False,
            "raises_assurance": False,
            "approves_merge": False,
        },
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
        return 0
    print("ORRO setup complete")
    print(f"home: {payload['home']}")
    print(f"depone_root: {payload['depone_root']}")
    print(f"depone_commit: {payload['depone_commit']}")
    print(f"engine_lock: {payload['engine_lock']}")
    print("next:")
    for step in payload["next_steps"]:
        print(f"  {step}")
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
