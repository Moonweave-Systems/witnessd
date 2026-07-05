# ORRO Continuation Gate v0

`orro next` is the non-executing continuation/status gate before future
`orro auto`.

```bash
orro next .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro next .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m witnessd orro next .witnessd/runs/<run-dir> --home .witnessd --json
```

It reads persisted artifacts and recommends the next safe action. It does not
execute workers, run proofcheck automatically, call live models, call MCP, repair
evidence, retry failed lanes, write handoff, approve merge, verify evidence, or
raise assurance.

Decision meanings:

- `needs-proofcheck`: run proofcheck next.
- `ready-for-handoff`: a passing bound `proofcheck-verdict.json` exists and
  handoff may be packaged.
- `complete`: handoff exists after proofcheck pass.
- `blocked`: do not continue without human or verifier intervention.
- `evidence-pending`: required run evidence is not present yet.
- `invalid-run-dir`: the supplied run directory is missing or unusable.

Observed artifacts include:

- `workflow-plan.json`
- `workflow-plan-binding.json`
- `role-lane-plan.json`
- `role-lane-plan-binding.json`
- `workflow-role-dispatch.json`
- `team-ledger.json`
- `team-ledger-verdict.json`
- `proofcheck-verdict.json`
- `orro-handoff.json`

Role status is derived from observed artifacts only. Runner roles may be
`executed` only when run evidence exists. The verifier role may be `verified`
only when an existing proofcheck verdict has `decision: "pass"` and is bound to
the current evidence snapshot. The handoff role may be `packaged` only when
`orro-handoff.json` exists.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

`orro next` is not a verifier. If verifier truth is missing, it reports
`needs-proofcheck` instead of re-deriving a verdict. A matching continuation
decision is not proof, approval, or assurance.

The next automation layer is `orro auto --dry-run`, which consumes this decision
and emits an `orro-auto-plan`. Dry-run may recommend a future proofcheck or
handoff command, but it does not run proofcheck, call Depone, write handoff,
launch workers, repair evidence, mutate worktrees, approve merge, verify
evidence, or raise assurance.

`orro auto --once` is the limited executor over the same continuation decision.
It re-checks state and executes at most one allowed step: proofcheck, handoff,
or complete no-op. It never launches proofrun or workers, repairs artifacts,
retries lanes, calls live models or MCP, approves merge, or raises assurance.
