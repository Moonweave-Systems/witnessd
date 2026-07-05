# ORRO Role-Lane Plan v0

`orro-role-lane-plan` is the bridge from ORRO rolepack intent to witnessd team
lanes. It is executable intent, not proof.

Typical path:

```bash
orro flowplan "fix parser bug" \
  --root . \
  --profile code-change \
  --out .witnessd/workflow-plan.json \
  --role-lanes-out .witnessd/role-lane-plan.json

orro proofrun "fix parser bug" \
  --repo . \
  --home .witnessd \
  --workflow-plan .witnessd/workflow-plan.json \
  --role-lane-plan .witnessd/role-lane-plan.json
```

Contract:

- `workflow_plan_hash` binds the role-lane plan to one workflow plan.
- `execution_allowed` must be true before proofrun may run lanes.
- `lanes` map executable ORRO roles to witnessd team lanes.
- default lanes use deterministic `shell`; live model adapters are not used by
  default.
- proofrun executes through existing witnessd team execution, observer, fan-in,
  and ledger machinery.
- proofcheck remains delegated to Depone.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Role-lane plans, workflow plans, role dispatch, review prose, and role names are
not evidence, not proof, not approval, and not assurance. Actual proof begins
only when proofrun emits evidence. Depone proofcheck decides what persisted
evidence supports. Handoff includes plan and role context only for review and
still requires a passing bound `proofcheck-verdict.json`.

Safety profiles:

- `code-change`: execution allowed.
- `docs-change`: execution allowed.
- `review-only`: execution not allowed.
- `verification-only`: execution not allowed.
- `release-readiness`: execution not allowed by default.

Broader autonomous `orro auto` and `orro ultra` remain future work.

Continuation:

```bash
orro next .witnessd/runs/<run-dir> --home .witnessd --json
```

`orro next` reads role-lane bindings, role dispatch, team ledger artifacts,
proofcheck verdicts, and handoff packages to recommend the next safe action. It
does not execute the role-lane plan, run proofcheck, retry lanes, repair
evidence, or raise assurance. Role status is derived from observed artifacts
only and is not proof.

`orro advise <goal>` may recommend skipping role-lane/team execution for
trivial, review-only, or verification-only work. That recommendation is
developer-judgment context only; it does not execute and does not replace
proofrun/proofcheck/handoff gates.

`orro auto --dry-run <run-dir> --home .witnessd --json` may consume the
continuation decision and emit an `orro-auto-plan` recommendation. It does not
execute role-lane plans, run proofcheck, call Depone, write handoff, retry
lanes, mutate worktrees, or raise assurance.

`orro auto --once <run-dir> --home .witnessd --json` may execute one safe next
step after re-checking continuation state, but v0 is limited to proofcheck,
handoff, or complete no-op. It never launches proofrun or workers and never
executes role-lane plans.

`orro auto --until-complete <run-dir> --home .witnessd --max-steps 2 --json` may
loop over those same post-run steps with a strict v0 bound. It never launches
proofrun or workers and never executes role-lane plans.
