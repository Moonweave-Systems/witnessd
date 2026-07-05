"""Run the witnessd-hosted ORRO surface as ``python3 -m orro``."""

from __future__ import annotations

import sys

from witnessd.__main__ import main as witnessd_main


ORRO_HELP = """usage: orro [-h] {scout,flowplan,proofrun,proofcheck,handoff,doctor,engine-lock} ...

ORRO - Observed Run & Review Orchestrator

ORRO Flow:
  scout -> flowplan -> proofrun -> proofcheck -> handoff

public commands:
  scout        read-only repository exploration and context packaging
  flowplan     plan-only workflow design; does not run workers
  proofrun     evidence-backed execution through witnessd
  proofcheck   offline evidence verification delegated to Depone
  handoff      maintainer review package gated by proofcheck-verdict.json
  doctor       ORRO readiness check; does not verify evidence
  engine-lock  write distribution metadata for pinned engine commits

boundary:
  Depone verifies; witnessd executes; ORRO exposes the workflow.
  engine-lock is metadata, not proof, approval, or assurance.

options:
  -h, --help   show this help message and exit
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(ORRO_HELP)
        return 0
    return witnessd_main(["orro", *args])


if __name__ == "__main__":
    sys.exit(main())
