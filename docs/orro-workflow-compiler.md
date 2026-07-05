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
```

The workflow plan is intent, not evidence. Roles do not create assurance by
existing. Model confidence, skill text, session transcript, MCP output alone,
doctor readiness, engine-lock metadata, and handoff prose are forbidden assurance
sources.

Phase ownership:

- `init`, `doctor`, and `engine-lock` are setup/readiness/distribution checks.
- `scout` and `flowplan` are planning phases.
- `proofrun` is the first execution phase and is owned by witnessd.
- `proofcheck` is the verifier phase and is delegated to Depone.
- `handoff` is review packaging only; it does not approve merge or raise
  assurance.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

The compiler does not run workers, call live models, call Depone verification,
mutate worktrees, approve merge, or turn ORRO into a third engine. Full
`orro auto` remains future work.
