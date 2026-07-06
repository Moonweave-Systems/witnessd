# ORRO Auto Dry-Run v0

`orro auto --dry-run` is the non-executing automation planner for the ORRO
continuation path.

Use `orro advise` before planning/execution when the user needs workstyle
guidance. `advise` chooses a smallest safe workflow; dry-run only plans the next
post-run continuation command from an existing run directory.

Use `orro report` when a human needs the compressed state of the run, the next
safe action, reviewer focus, and do-not-trust boundaries. Report is a summary,
not proof or verifier truth.

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

The paired limited execution mode is:

```bash
orro auto --once .witnessd/runs/<run-dir> --home .witnessd --json
```

`--once` re-checks continuation state and executes at most one allowed step:
proofcheck, handoff, or complete no-op. It does not launch proofrun or workers,
repair artifacts, retry or resume lanes, call live models or MCP, approve merge,
or raise assurance.

The paired bounded post-run loop is:

```bash
orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
```

`--until-complete` re-checks continuation state before every step and may run
only proofcheck and handoff. It never launches proofrun or workers, repairs,
retries, resumes lanes, calls live models or MCP, approves merge, or raises
assurance. Its `orro-auto-session` is orchestration metadata only.

Decision mapping:

- `needs-proofcheck`: recommend proofcheck.
- `ready-for-handoff`: recommend handoff.
- `complete`: no-op, no commands.
- `blocked`: no commands; stop for human or verifier intervention.
- `evidence-pending`: no commands; do not guess execution.
- `invalid-run-dir`: fail closed.

Malformed, stale, copied, or unbound critical artifacts are blocked by
`orro next`, so dry-run emits no command for those states. Dry-run must not turn
a malformed team ledger, corrupted binding, stale proofcheck verdict, or copied
handoff package into an executable recommendation.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

The auto-plan is recommendation context only. It is not proof, not evidence
verification, not merge approval, and not assurance. Calling `orro auto` without
exactly one mode must fail closed.
