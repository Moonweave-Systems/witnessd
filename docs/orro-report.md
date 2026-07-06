# ORRO Report v0

`orro report` is the human-facing compression layer for a persisted ORRO run:

```bash
orro report .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro report .witnessd/runs/<run-dir> --home .witnessd
python3 -m witnessd orro report .witnessd/runs/<run-dir> --home .witnessd --json
```

It summarizes what ORRO can observe from existing artifacts:

- goal and workflow/profile context
- workstyle decision context when supplied with `--workstyle-decision`
- proofrun evidence presence and lane count
- proofcheck verdict summary as written by Depone
- handoff package presence
- continuation decision and next safe action
- reviewer focus and do-not-trust boundaries

The JSON artifact kind is `orro-report` with schema version `0.1`. Optional
`--out` writes the same payload to disk.

## Boundaries

The report is summary context only. It does not execute workers, run proofcheck,
package handoff, call live models, call MCP, mutate worktrees except explicit
`--out`, approve merge, or raise assurance. It may quote an existing
`proofcheck-verdict.json`, but it does not rederive verifier truth. Depone
proofcheck remains the verifier.

`ready-for-handoff` means a passing bound proofcheck verdict exists and handoff
can be packaged. `complete` means handoff exists after proofcheck pass and is
bound to the current run directory and current proofcheck verdict. A report must
not report ready or complete for scout-only directories, stale verdicts, unbound
proofcheck verdicts, or stale/copied handoff artifacts.

## Human Review

Report output should make the next action obvious and reduce artifact fatigue:

- inspect changed files and lane evidence
- inspect `proofcheck-verdict.json`
- package or inspect `orro-handoff.json` before merge
- do not trust workflow plans, role-lane plans, role names, transcripts, model
  confidence, engine locks, or handoff prose as proof

Depone verifies; witnessd executes; ORRO exposes the workflow.
