# ORRO Workstyle Doctrine v0

`orro advise` is the non-executing developer-judgment layer for ORRO.

It recommends the smallest safe workflow for a goal before execution begins:

```bash
orro advise "fix parser bug" --repo . --home .witnessd --json
python3 -m orro advise "review this PR" --repo . --home .witnessd --json
python3 -m witnessd orro advise "verify existing evidence" --repo . --home .witnessd --json
```

The output is an `orro-workstyle-decision`. It is advice, not proof, verifier
truth, merge approval, or assurance. It does not execute commands, call Depone,
call live models, launch workers, mutate worktrees, or approve merge.

## Doctrine

- Prefer the smallest safe workflow.
- Do not run agents just because agents are available.
- Do not read the entire repo when scoped discovery is enough.
- Do not use role-lane or team execution for trivial edits unless evidence is
  explicitly required.
- Do not use proofrun for review-only or verification-only tasks.
- Do not let LLM confidence replace proofcheck.
- Do not treat tests alone as final truth.
- Do not treat workflow plans, role-lane plans, role names, auto plans, auto
  receipts, auto sessions, or handoff prose as assurance.
- Stop when the next safe action is proofcheck, handoff, human review, or
  blocked.
- Escalate risky changes to human review.
- Use bounded auto only after proofrun evidence exists.

Depone verifies; witnessd executes; ORRO exposes the workflow.

`orro report <run-dir> --home .witnessd --json` is the post-run counterpart to
`orro advise`. Advice reduces waste before planning or execution; report reduces
artifact fatigue after a run by summarizing observed state, next safe action,
reviewer focus, and do-not-trust boundaries. Report is not proof, verifier
truth, approval, or assurance.

## Built-In Task Classes

- `trivial-change`: minimal effort; skip team execution unless evidence is
  explicitly required.
- `docs-change`: bounded docs workflow.
- `code-change`: bounded scout, flowplan, proofrun, proofcheck, handoff path.
- `review-only`: no proofrun recommendation.
- `verification-only`: recommend proofcheck over proofrun.
- `release-readiness`: prefer init, doctor, engine-lock, next, and report-style
  checks; readiness is not assurance.
- `risky-change`: require human review and avoid auto proofrun.
- `unknown`: scout and flowplan first; do not recommend execution until narrowed.

The v0 classifier is deterministic and transparent. It returns `rule_matches`
and `reasons` instead of pretending the classification is perfect. Future
adaptive or LLM-based routing must remain policy-gated and advisory unless a
separate execution gate explicitly authorizes action.
