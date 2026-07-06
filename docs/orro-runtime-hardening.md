# ORRO Runtime Hardening

ORRO runtime hardening prevents partial, stale, copied, malformed, or
contradictory artifacts from looking like successful progress.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

## Fail-Closed Rules

ORRO continuation surfaces fail closed when critical run artifacts are missing,
malformed, stale, copied, or unbound. In particular:

- malformed workflow plans, workflow-plan bindings, role-lane plans,
  role-lane bindings, role dispatch, team ledgers, or team-ledger verdicts block
  continuation;
- scout-only directories do not count as execution evidence;
- a missing `proofcheck-verdict.json` can produce `needs-proofcheck` only when
  real run evidence exists;
- malformed, non-object, non-pass, unbound, stale, or copied
  `proofcheck-verdict.json` artifacts block handoff and auto handoff;
- `complete` requires `orro-handoff.json` to match the current run directory and
  current proofcheck verdict;
- stale or copied handoff packages block `next`, `report`, and `auto`.

These checks are runtime safety checks only. They do not duplicate Depone
verification and do not rederive verifier truth.

## Non-Executing Surfaces

The following surfaces read or summarize state and must not create proof:

- `orro advise`
- `orro next`
- `orro report`
- `orro auto --dry-run`

They must not launch workers, create run directories, emit team ledgers, write
proofcheck verdicts, package handoff, approve merge, or raise assurance. Their
optional `--out` files are decision or summary metadata only.

## Bounded Auto

`orro auto --once` and `orro auto --until-complete` re-check continuation state
before acting. In v0 they may only run proofcheck and handoff after proofrun
evidence exists. They never run proofrun, launch workers, repair artifacts,
retry lanes, resume lanes, call live models, or call MCP.

Auto receipts and sessions are orchestration metadata. They are not proof,
verifier truth, approval, or assurance.

## Reviewer Meaning

Runtime hardening is intended to keep ORRO honest under broken states. A blocked
state means the next safe action is human or verifier intervention, not a
stronger automation loop.
