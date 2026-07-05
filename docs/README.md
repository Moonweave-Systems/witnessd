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
| ORRO workflow compiler v0 | [`orro-workflow-compiler.md`](orro-workflow-compiler.md) |

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
| `orro scout` | read-only repo exploration and context packaging |
| `flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `proofrun` | evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro auto` | continuation mode behind evidence gates |
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
