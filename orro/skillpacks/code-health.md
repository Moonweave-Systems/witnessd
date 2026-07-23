---
name: orro-code-health
mode: code-health
triggers: health, code health, lint, format, typecheck, --health, --fix, --apply, --init, --promote
boundary: advisory-and-verified-checks
---

# ORRO code health

Use this reference when a goal needs deterministic repository health gates. It
describes the existing `orro check` health axis; it does not add a new verdict
source or replace Depone.

`orro check --health` detects the repository's configured gates and runs them
under observation. `--init` seeds missing health configuration/profile data;
`--promote` changes an existing gate's enforcement tier and requires the
profile to exist. `--health-plan` prints the detected plan without running it.

Safe fixers require both `--fix` and explicit repeatable `--write-scope`
values. `--apply` is valid only with `--fix`: the fixer runs in a bounded lane,
Depone checks the write scope, and only the verified diff may be applied to the
caller worktree. Missing gates, missing profiles, unavailable tools, or failed
scope/policy checks remain structured blockers; do not infer a gate or upgrade
an advisory signal into a verdict.

The health section in the companion manifest reports observed gate exits,
enforcement, fixer diff references, and the boundary that health is not a claim
of design quality or structural consistency.
