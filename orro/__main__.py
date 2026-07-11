"""Deprecated witnessd-hosted ``orro`` shim."""

from __future__ import annotations

import sys

from witnessd.__main__ import main as witnessd_main


DEPRECATION_WARNING = (
    "warning: witnessd-hosted orro is deprecated; install the ORRO package for "
    "the orro command. This shim will be removed in the next major witnessd release."
)

ORRO_HELP = """usage: orro [-h] {init,advise,scout,flowplan,proofrun,proofcheck,handoff,next,report,review,auto,doctor,engine-lock} ...

ORRO - Observed Run & Review Orchestrator

ORRO Flow:
  scout -> flowplan -> proofrun -> proofcheck -> handoff

public commands:
  init         setup readiness/provision metadata; does not verify evidence
  advise       non-executing workstyle router for the smallest safe workflow
  scout        read-only repository exploration and context packaging
  flowplan     plan-only workflow design; does not run workers
  proofrun     evidence-backed execution through witnessd
  proofcheck   offline evidence verification delegated to Depone
  handoff      maintainer review package gated by proofcheck-verdict.json
  next         non-executing continuation gate over persisted run artifacts
  report       human-facing summary of observed ORRO artifacts and next action
  review       advisory read-only reviewer lanes; not proof or assurance
  auto         dry-run, one-step, or bounded post-run automation
  doctor       ORRO readiness check; does not verify evidence
  engine-lock  write/check distribution metadata for pinned engine commits

boundary:
  Depone verifies; witnessd executes; ORRO exposes the workflow.
  advise, next, and report read status/intent only; auto --dry-run recommends
  commands only; auto --once executes at most one proofcheck or handoff step;
  auto --until-complete loops over those post-run steps with --max-steps. None
  is proof or assurance.

options:
  -h, --help   show this help message and exit
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print(DEPRECATION_WARNING, file=sys.stderr)
    if not args or args[0] in {"-h", "--help"}:
        print(ORRO_HELP)
        return 0
    return witnessd_main(["orro", *args])


if __name__ == "__main__":
    sys.exit(main())
