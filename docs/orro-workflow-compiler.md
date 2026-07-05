# ORRO Workflow Compiler v0

ORRO has a deterministic rolepack/workflow compiler v0. It maps a user goal and
built-in profile into an `orro-workflow-plan` intent artifact.

`orro advise` sits before this compiler as the workstyle router. It may
recommend a profile and path, but its `orro-workstyle-decision` is advice only:
not proof, not verifier truth, not approval, and not assurance.

Supported profiles:

- `code-change`
- `review-only`
- `verification-only`
- `docs-change`
- `release-readiness`

Example:

```bash
python3 -m orro flowplan "fix bug in parser" --root . --profile code-change --out workflow-plan.json
python3 -m orro flowplan "fix bug in parser" --root . --profile code-change --role-lanes-out role-lane-plan.json
python3 -m orro proofrun "fix bug in parser" --repo . --home .witnessd --workflow-plan workflow-plan.json --role-lane-plan role-lane-plan.json
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

When `flowplan` receives `--role-lanes-out`, it also writes an
`orro-role-lane-plan` artifact. That artifact maps executable workflow roles to
witnessd team lanes and records the workflow plan hash. It is executable intent,
not proof. When `proofrun` receives both `--workflow-plan` and
`--role-lane-plan`, it validates the role-lane plan hash, the workflow phase
gate, `execution_allowed`, and the executable lane list before creating a run
directory. Execution still happens through existing witnessd team machinery.
Proofcheck and handoff may preserve role-lane binding references for review
context only.

After proofrun, `orro next <run-dir> --home <home> --json` can inspect those
persisted references and recommend the next safe action. It is a non-executing
continuation gate: it does not run proofcheck, execute workers, retry lanes,
repair evidence, write handoff, approve merge, verify evidence, or raise
assurance. It reports `needs-proofcheck`, `ready-for-handoff`, `complete`,
`blocked`, `evidence-pending`, or `invalid-run-dir` from observed artifacts
only.

`orro auto --dry-run <run-dir> --home <home> --json` can consume that
continuation state and emit an `orro-auto-plan` with the exact next command it
would run. It is recommendation context only: it does not execute the command,
call Depone, launch workers, verify evidence, approve merge, or raise
assurance.

`orro report <run-dir> --home <home> --json` can summarize the resulting
workflow plan binding, role-lane binding, role dispatch, evidence, proofcheck,
handoff, continuation, auto metadata, and reviewer focus. It is human-facing
summary context only and must not be treated as proof, verifier truth, approval,
or assurance.

`orro auto --once <run-dir> --home <home> --json` re-checks continuation state
and executes at most one allowed step. In v0, that means proofcheck, handoff, or
complete no-op only. It does not launch proofrun or workers, execute role-lane
plans, retry or resume lanes, call live models or MCP, approve merge, or raise
assurance.

`orro auto --until-complete <run-dir> --home <home> --max-steps 2 --json` is the
bounded post-run loop over proofcheck and handoff only. It re-checks state before
every step and never launches proofrun or workers. Its auto session is
orchestration metadata, not proof.

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

`verification-only` points at proofcheck/handoff intent over existing evidence;
it does not launch proofrun. Default `release-readiness` role-lane plans are
readiness intent and also do not launch proofrun.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

The compiler does not run workers, call live models, call Depone verification,
mutate worktrees, approve merge, or turn ORRO into a third engine. Broader
autonomous `orro auto` and `orro ultra` remain future work.
