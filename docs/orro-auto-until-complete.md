# ORRO Auto Until-Complete v0

`orro auto --until-complete` is bounded post-run automation over the existing
`orro next` continuation gate.

It is not adaptive routing. Use `orro advise` before planning/execution to get a
non-executing workstyle decision. Until-complete only loops over proofcheck and
handoff after proofrun evidence already exists.

Use `orro report` after dry-run, once, or until-complete when a reviewer needs a
single human-facing summary of observed artifacts, next safe action, blocked
state, and trust boundaries. Report does not execute or verify.

```bash
orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
python3 -m orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
python3 -m witnessd orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
```

It requires `--max-steps`. In v0, accepted values are `1` and `2`; unbounded
loops are not supported.

Allowed steps:

- `needs-proofcheck`: run proofcheck once and write `proofcheck-verdict.json`.
- `ready-for-handoff`: run handoff once and write `orro-handoff.json`.
- `complete`: stop successfully without rewriting proofcheck or handoff, only
  after the existing handoff artifact matches the current run and current
  `proofcheck-verdict.json`.

It re-checks continuation state before every step and derives commands from
observed run-directory state, not from stale auto-plan or receipt files. It
stops on `complete`, `blocked`, `evidence-pending`, `invalid-run-dir`, or
`max-steps` reached.

Copied, stale, malformed, or unbound `orro-handoff.json` artifacts block the
loop instead of being treated as complete.

Malformed workflow bindings, role-lane bindings, role dispatch, team ledgers,
team-ledger verdicts, or proofcheck verdicts also block the loop. The loop must
not infer proofrun success from artifact names alone and must not continue from
stale auto-plan, receipt, or session files.

It never launches proofrun or workers. It does not call live models, call MCP,
call live APIs, execute recipes, repair artifacts, retry failed lanes, resume
lanes, approve merge, or raise assurance. It may mutate only the explicit
proofcheck or handoff output files caused by allowed steps, plus an explicit
`--out` auto-session path when requested.

When it runs proofcheck, verification is delegated to Depone. ORRO does not
verify evidence itself.

The `orro-auto-session` is orchestration metadata. It records the initial
decision, final decision, bounded steps, commands, exit codes, and files written.
It is not proof of task success, not verifier truth, not merge approval, and not
assurance.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Broader autonomous `orro auto` and `orro ultra` remain future work.
