"""Deprecated witnessd-hosted ``orro`` shim."""

from __future__ import annotations

import os
import sys
from difflib import get_close_matches

from witnessd.__main__ import ORRO_COMMANDS, main as witnessd_main


DEPRECATION_WARNING = (
    "warning: witnessd-hosted orro is deprecated; install the ORRO package with "
    "pip install \"orro>=0.0.2\" for the orro command. This shim will be removed "
    "in the next major witnessd release."
)

ORRO_HELP = """usage: orro [-h] {setup,init,advise,scout,sketch,trace,flow,flowplan,proofrun,proofcheck,advisory-provenance-check,handoff,next,report,review,auto,team,doctor,engine-lock} ...

ORRO - Observed Run & Review Orchestrator

ORRO Flow:
  scout -> sketch/trace -> flowplan -> proofrun -> proofcheck -> handoff

public commands:
  setup       provision pinned Depone, initialize home, and write engine lock
  init         setup readiness/provision metadata; does not verify evidence
  advise       non-executing workstyle router for the smallest safe workflow
  scout        read-only repository exploration and context packaging
  sketch       validate and seal an agent-authored advisory direction
  trace        validate, gate, and seal an agent-authored root-cause record
  flow         guided init/scout/flowplan/proofrun/proofcheck with gated blockers
  flowplan     plan-only workflow design; does not run workers
  proofrun     evidence-backed execution through witnessd
  proofcheck   offline evidence verification delegated to Depone
  advisory-provenance-check
               offline Depone v110 re-derivation of sealed advisory provenance
  handoff      maintainer review package gated by proofcheck-verdict.json
  next         non-executing continuation gate over persisted run artifacts
  report       human-facing summary of observed ORRO artifacts and next action
  review       advisory read-only reviewer lanes; not proof or assurance
  auto         dry-run, one-step, or bounded post-run automation
  team         scaffold team config or run flowplan/proofrun/proofcheck/report
  doctor       ORRO readiness check; does not verify evidence
  engine-lock  write/check distribution metadata for pinned engine commits

boundary:
  Depone verifies; witnessd executes; ORRO exposes the workflow.
  advise, sketch, trace, next, and report read status/intent only; trace consumes a
  symptom-bound prior-run receipt without executing repo code; auto --dry-run recommends
  commands only; auto --once executes at most one proofcheck or handoff step;
  auto --until-complete loops over those post-run steps with --max-steps. None
  is proof or assurance. A provenance PASS means sealed bytes are internally
  re-derivable; it does not establish that a direction or root cause is correct.

options:
  -h, --help   show this help message and exit
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("ORRO_WRAPPER_DELEGATION") != "1":
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
