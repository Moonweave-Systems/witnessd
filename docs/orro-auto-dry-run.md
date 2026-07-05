# ORRO Auto Dry-Run v0

`orro auto --dry-run` is the non-executing automation planner before any
executing `orro auto` mode.

```bash
orro auto --dry-run .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro auto --dry-run .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m witnessd orro auto --dry-run .witnessd/runs/<run-dir> --home .witnessd --json
```

It consumes the existing `orro next` continuation decision and emits an
`orro-auto-plan`. The plan may recommend the exact next command, such as:

```bash
orro proofcheck <run-dir> --home <home> --out <run-dir>/proofcheck-verdict.json
orro handoff <run-dir> --out <run-dir>/orro-handoff.json
```

Dry-run does not run those commands. It does not call Depone, launch workers,
write proofcheck verdicts, write handoff packages, repair evidence, retry lanes,
mutate worktrees, approve merge, verify evidence, or raise assurance. A
recommended proofcheck command would verify evidence if a user runs it later;
the dry-run itself does not verify evidence.

Decision mapping:

- `needs-proofcheck`: recommend proofcheck.
- `ready-for-handoff`: recommend handoff.
- `complete`: no-op, no commands.
- `blocked`: no commands; stop for human or verifier intervention.
- `evidence-pending`: no commands; do not guess execution.
- `invalid-run-dir`: fail closed.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

The auto-plan is recommendation context only. It is not proof, not evidence
verification, not merge approval, and not assurance. Calling `orro auto` without
`--dry-run` must fail closed until executing automation is implemented as a
separate gated mode.
