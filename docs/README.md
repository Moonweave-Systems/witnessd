# witnessd Documentation Map

This file exists to prevent doc drift. It is a map, not a second spec.

## Canonical docs

| Role | Document |
| --- | --- |
| Product/runtime source of truth | [`../SPEC3.md`](../SPEC3.md) |
| Depone verifier-contract authority | Depone `docs/spec.md` |
| Human quickstart | [`../README.md`](../README.md) |
| Host session skill | [`../SKILL.md`](../SKILL.md) |
| Codex session guidance | [`../AGENTS.md`](../AGENTS.md) |
| Agent/developer orientation | [`../CLAUDE.md`](../CLAUDE.md) |
| ORRO workstyle doctrine v0 | [`orro-workstyle-doctrine.md`](orro-workstyle-doctrine.md) |
| ORRO product reality check | [`orro-product-reality-check.md`](orro-product-reality-check.md) |
| ORRO report v0 | [`orro-report.md`](orro-report.md) |
| ORRO workflow compiler v0 | [`orro-workflow-compiler.md`](orro-workflow-compiler.md) |
| ORRO continuation gate v0 | [`orro-continuation-gate.md`](orro-continuation-gate.md) |
| ORRO auto dry-run v0 | [`orro-auto-dry-run.md`](orro-auto-dry-run.md) |
| ORRO auto once v0 | [`orro-auto-once.md`](orro-auto-once.md) |
| ORRO auto until-complete v0 | [`orro-auto-until-complete.md`](orro-auto-until-complete.md) |

When these conflict, `SPEC3.md` wins for witnessd runtime/product decisions.
Depone `docs/spec.md` wins for verifier-contract decisions.

## Public naming

Use these names in new user-facing docs:

| Name | Meaning |
| --- | --- |
| Moonweave / Moonweave Systems | publisher/account namespace |
| ORRO | flagship product/tool, published by Moonweave |
| Observed Run & Review Orchestrator | ORRO full name |
| ORRO Flow | scout -> flowplan -> proofrun -> proofcheck -> handoff |
| `orro` | primary command/skill surface |
| `orro init` | setup readiness/provision metadata; not proof or assurance |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo exploration and context packaging |
| `flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `proofrun` | evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation/status gate over persisted run artifacts |
| `orro report` | human-facing summary of observed ORRO artifacts and next safe action |
| `orro auto --dry-run` | non-executing automation planner; recommendation context only |
| `orro auto --once` | one-step proofcheck/handoff executor; orchestration metadata only |
| `orro auto --until-complete` | bounded post-run loop over proofcheck and handoff only |
| `orro auto` | future broader continuation mode behind evidence gates |
| `orro ultra` | future high-autonomy profile |
| `python3 -m orro` | thin product-name entrypoint hosted in witnessd |
| `orro engine-lock` | write/check distribution metadata for pinned witnessd and Depone commits |
| Superflow | historical/compatibility name, superseded by ORRO |

Use `witnessd` only when discussing the engine or CLI. Use `Moonweave` only when
discussing the publisher/account namespace.

The current ORRO entrypoint is not a standalone ORRO repository and not a third
engine. Its help text is product-facing and lists only public ORRO commands. It
delegates subcommands to the witnessd-hosted ORRO command surface. Public setup
starts with `orro init`, which delegates to witnessd initialization/provisioning
and creates readiness metadata such as `.witnessd/provision.json`; it is not
proof or assurance. `orro doctor` checks readiness, not evidence truth. The
engine lock is distribution metadata only. `--out` writes the pinned commit
metadata; `--check` detects local environment drift against it. A matching lock
is readiness alignment only, not evidence verification, merge approval, or an
assurance increase. A mismatch is readiness-blocked, not verifier-refuted. A
standalone ORRO repo remains deferred until packaging, marketplace, or
version-lock distribution needs justify it. The bare `orro` console script
points at `orro.__main__:main` and remains an alias layer over the same product
surface.

`orro advise "<goal>" --repo <repo> --home <home> --json` is the developer
judgment/workstyle layer. It recommends the smallest safe workflow for the goal
and returns an `orro-workstyle-decision`. It is non-executing advice: not proof,
not verifier truth, not approval, and not assurance. It helps non-developers
avoid wasteful or risky AI workflows, but it does not replace proofrun,
proofcheck, handoff, or human review for risky changes.

`orro flowplan --profile <profile>` emits an `orro-workflow-plan` intent
artifact for deterministic profiles such as `code-change`, `review-only`,
`verification-only`, `docs-change`, and `release-readiness`. Workflow plans are
not evidence, roles do not create assurance by existing, `proofrun` is the first
execution phase, `proofcheck` is the verifier phase, and `handoff` is review
packaging only.

`proofrun --workflow-plan <path>` gates execution against that intent before any
run directory is created. The plan must allow `proofrun` through a witnessd
engine call that executes and does not verify. If allowed, proofrun records
`workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json` in the run directory. These artifacts are review
context only. They are not proof, verification, approval, or assurance, and
formal `orro handoff` still requires a passing bound proofcheck verdict.

`flowplan --role-lanes-out <path>` also writes an `orro-role-lane-plan` that
maps executable roles to witnessd team lanes. `proofrun --role-lane-plan <path>`
validates the hash binding and execution gate, then reuses existing witnessd
team execution. Role-lane plans are executable intent, not proof; review-only
and verification-only plans cannot launch proofrun.

See [`orro-role-lane-plan.md`](orro-role-lane-plan.md) for the artifact
contract.

`orro next <run-dir> --home <home> --json` reads persisted run artifacts and
recommends the next safe action. It is non-executing: it does not run proofcheck,
launch workers, repair evidence, retry lanes, write handoff, verify evidence,
approve merge, or raise assurance. `needs-proofcheck` means run proofcheck next;
`ready-for-handoff` means a passing bound proofcheck verdict exists; `complete`
means handoff exists after proofcheck pass; `blocked` means do not continue
without human/verifier intervention. Role status is derived context, not proof.

`orro report <run-dir> --home <home> --json` is the human-facing compression
layer for a run directory. It summarizes observed artifacts, proofcheck state,
handoff state, next safe action, reviewer focus, and do-not-trust boundaries.
It reduces artifact fatigue for non-developers and reviewers, but it does not
execute, verify evidence, approve merge, raise assurance, replace proofcheck, or
replace human review.

`orro auto --dry-run <run-dir> --home <home> --json` consumes that continuation
state and emits an `orro-auto-plan` with the exact command it would run next. It
does not run the command, call Depone, launch workers, write proofcheck verdicts
or handoff packages, mutate worktrees, verify evidence, approve merge, or raise
assurance. The auto-plan is recommendation context only, not proof. Broader
autonomous `orro auto` remains future work.

`orro auto --once <run-dir> --home <home> --json` re-checks continuation state
and executes at most one allowed step: proofcheck, handoff, or complete no-op.
It never launches proofrun or workers, calls live models or MCP, repairs
artifacts, retries or resumes lanes, approves merge, or raises assurance. The
auto receipt is orchestration metadata only, not proof or verifier truth.

`orro auto --until-complete <run-dir> --home <home> --max-steps 2 --json` is a
bounded post-run loop over the same safe steps. It requires `--max-steps`; v0
allows only 1 or 2. It re-checks continuation state before every step and may
run proofcheck then handoff, but never proofrun or workers. The auto session is
orchestration metadata only, not proof, verifier truth, approval, or assurance.

## Legacy docs

The following categories are historical or wave-specific unless `SPEC3.md`
explicitly promotes them:

- `SPEC.md` and `SPEC2.md` - foundation history,
- `docs/plans/*` - wave notes and acceptance artifacts,
- `docs/conformance/*` - conformance notes derived from implemented evidence,
- fixture notes - explanations of committed evidence,
- old release notes - historical state,
- Superflow naming - compatibility history, not the current public product name.

Do not start new work from a legacy doc. Start from `SPEC3.md`, then follow the
current wave's acceptance bar.

## Edit rule

When product direction, architecture, layer ownership, skill naming, or the
roadmap changes:

1. update `SPEC3.md`,
2. update this map if the canonical set changed,
3. update README / SKILL / AGENTS / CLAUDE as derived summaries,
4. leave legacy docs as historical unless they actively mislead current work.
