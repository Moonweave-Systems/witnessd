from __future__ import annotations

import argparse
import sys


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
