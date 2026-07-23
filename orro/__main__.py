"""Deprecated witnessd-hosted ``orro`` shim."""

from __future__ import annotations

import os
import sys
from difflib import get_close_matches
from pathlib import Path

from witnessd.__main__ import (
    ORRO_COMMAND_MAP,
    ORRO_COMMANDS,
    PUBLIC_COMMAND_SUMMARIES,
    main as witnessd_main,
)


DEPRECATION_WARNING = (
    "warning: witnessd-hosted orro is deprecated; install the ORRO package with "
    "pip install \"orro>=0.0.2\" for the orro command. This shim will be removed "
    "in the next major witnessd release."
)


def _build_orro_help(
    *,
    command_map: dict[str, str] = ORRO_COMMAND_MAP,
    summaries: dict[str, str] = PUBLIC_COMMAND_SUMMARIES,
) -> str:
    command_names = ",".join(command_map)
    width = max(map(len, command_map))
    public_commands = "\n".join(
        f"  {command:<{width}}  {summaries[command]}"
        for command in command_map
    )
    return f"""usage: orro [-h] {{{command_names}}} ...

ORRO - Observed Run & Review Orchestrator

ORRO Flow:
  scout -> sketch/trace -> flowplan -> proofrun -> proofcheck -> handoff

public commands:
{public_commands}

boundary:
  Depone verifies; witnessd executes; ORRO exposes the workflow.
  advise, sketch, trace, next, and report read status/intent only; trace consumes a
  symptom-bound prior-run receipt without executing repo code; auto --dry-run recommends
  commands only; auto --once executes at most one proofcheck or handoff step;
  auto --until-complete loops over those post-run steps with --max-steps; auto
  --run-item executes the next declared step's recommended command behind
  evidence gates, bounded by --max-steps, and stops at the first non-pass. None
  is proof or assurance. A provenance PASS means sealed bytes are internally
  re-derivable; it does not establish that a direction or root cause is correct.

options:
  -h, --help   show this help message and exit
  --write-scope '<glob>' (repeatable): bounded write scope for a code-change plan; generates the role capability directly instead of requiring a prebuilt rolepack. Never inferred or defaulted.
  --command '<shell>' (repeatable, --lane-adapter shell only): declared deterministic commands the runner executes; touched files are checked against --write-scope. Not for AI adapters.
  --role-lane-tier
               auto (default): shell lanes run at quick/120s, AI-adapter lanes at agentic/1800s; override with quick|agentic|frontier
  --runner-sandbox DIR
               filesystem DIR where the runner executes; NOT a Codex sandbox mode (read-only/workspace-write) and NOT the observer run/out directory
"""


ORRO_HELP = _build_orro_help()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    module_invocation = Path(sys.argv[0]).name == "__main__.py"
    if not module_invocation and os.environ.get("ORRO_WRAPPER_DELEGATION") != "1":
        print(DEPRECATION_WARNING, file=sys.stderr)
    if not args or args[0] in {"-h", "--help"}:
        print(ORRO_HELP)
        return 0
    command = args[0]
    if command not in ORRO_COMMANDS:
        print(f"orro: unknown command '{command}'", file=sys.stderr)
        suggestion = get_close_matches(command, sorted(ORRO_COMMANDS), n=1)
        if suggestion:
            print(f"did you mean '{suggestion[0]}'?", file=sys.stderr)
        print(
            "valid commands: " + ", ".join(sorted(ORRO_COMMANDS)),
            file=sys.stderr,
        )
        return 2
    return witnessd_main(["orro", *args])


if __name__ == "__main__":
    sys.exit(main())
