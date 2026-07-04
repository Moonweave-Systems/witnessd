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
| `orro scout` | read-only repo exploration and context packaging |
| `flowplan` | plan-only workflow design |
| `proofrun` | evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro auto` | continuation mode behind evidence gates |
| `orro ultra` | future high-autonomy profile |
| Superflow | historical/compatibility name, superseded by ORRO |

Use `witnessd` only when discussing the engine or CLI. Use `Moonweave` only when
discussing the publisher/account namespace.

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
