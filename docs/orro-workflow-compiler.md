# ORRO Workflow Compiler v0

ORRO has a deterministic rolepack/workflow compiler v0. It maps a user goal and
built-in profile into an `orro-workflow-plan` intent artifact.

Supported profiles:

- `code-change`
- `review-only`
- `verification-only`
- `docs-change`
- `release-readiness`

Example:

```bash
python3 -m orro flowplan "fix bug in parser" --root . --profile code-change --out workflow-plan.json
python3 -m orro proofrun "fix bug in parser" --repo . --home .witnessd --workflow-plan workflow-plan.json
```

The workflow plan is intent, not evidence. Roles do not create assurance by
existing. Model confidence, skill text, session transcript, MCP output alone,
doctor readiness, engine-lock metadata, and handoff prose are forbidden assurance
sources.

When `proofrun` receives `--workflow-plan`, it first applies a phase gate. The
plan must include `proofrun` in `flow` and must include a witnessd `proofrun`
engine call with `executes: true` and `verifies: false`. If the gate fails,
proofrun fails closed before it creates a run directory. Phase gates constrain
which ORRO phase may run; they do not verify evidence.

When the gate passes, proofrun records the normalized plan, a
`workflow-plan-binding.json` hash reference, and `workflow-role-dispatch.json` in
the run directory. The binding states which workflow the run intended to follow.
The role dispatch artifact maps workflow roles to actual or pending engine
phases and may reference `team-ledger.json` and lane ids when those exist. These
artifacts are not proof that execution followed the plan, not evidence
verification, not approval, and not assurance. Actual execution proof still
begins with proofrun evidence, and Depone proofcheck still decides what that
evidence supports. Proofcheck and handoff may include the binding and role
dispatch references for review context only.

Phase ownership:

- `init`, `doctor`, and `engine-lock` are setup/readiness/distribution checks.
- `scout` and `flowplan` are planning phases.
- `proofrun` is the first execution phase and is owned by witnessd.
- `proofcheck` is the verifier phase and is delegated to Depone.
- `handoff` is review packaging only; it does not approve merge or raise
  assurance.

`review-only` is review intent only. It does not authorize proofrun, and it does
not imply that the formal `orro handoff` command can run without proofcheck. A
formal `orro handoff` artifact still requires a passing bound
`proofcheck-verdict.json`.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

The compiler does not run workers, call live models, call Depone verification,
mutate worktrees, approve merge, or turn ORRO into a third engine. Full
`orro auto` remains future work.
