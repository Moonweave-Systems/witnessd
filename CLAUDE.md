# witnessd - Agent Context

`witnessd` is the executing runtime engine in the ORRO pair. It spawns workers,
owns durable sessions, creates worktrees, retries, supervises teams, records
verification and MCP/tool receipts, and emits observer-signed evidence. Depone is
the non-executing verifier that re-derives the verdict from those bytes.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Moonweave is the publisher/account namespace. ORRO is the product/tool name.
`Superflow` is historical/compatibility naming and should not be used for new
public docs.

## Source of truth

[`SPEC3.md`](SPEC3.md) is the only top-level witnessd product/runtime authority.
`SPEC.md`, `SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`,
`AGENTS.md`, fixture notes, and release notes are derived, wave-specific, or
historical. If they conflict with `SPEC3.md`, `SPEC3.md` wins.

For the Depone verifier contract itself, Depone's `docs/spec.md` is the
authority. See [`docs/README.md`](docs/README.md) for the witnessd documentation
map and legacy policy.

## Public names

| Public surface | Purpose |
| --- | --- |
| ORRO | Observed Run & Review Orchestrator; flagship product/tool |
| ORRO Flow | scout -> flowplan -> proofrun -> proofcheck -> handoff |
| `orro` | flagship goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro scout` | read-only repo profile, context pack, and discovery notes |
| `flowplan` | plan-only workflow design |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro skillpack` | knowledge-as-code and progressive-disclosure support |
| `orro doctor` | engine/verifier/adapter/key/MCP/policy readiness check |
| `orro auto` | later resume/continuation loop behind evidence gates |
| `orro ultra` | future high-autonomy profile with stricter gates |

`witnessd` is the engine name, not the main session skill name.

## Entrypoint and repository boundary

`python3 -m orro ...` is a thin product-name entrypoint hosted in this witnessd
repo. It delegates to the existing `witnessd orro ...` surfaces and does not add
execution or verifier logic. It is not a standalone ORRO repo and not a third
engine.
`python3 -m orro --help` is ORRO-facing and lists only public ORRO commands, not
witnessd engine-internal commands.

`python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json`
writes distribution metadata for the pinned witnessd and Depone commits.
`python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json`
checks the current local environment for drift against that metadata. A matching
lock is readiness alignment only. A mismatch is readiness-blocked, not
verifier-refuted. The engine lock is not proof, does not verify evidence, does
not approve merge, and does not raise assurance.

A standalone ORRO repo remains deferred until packaging, marketplace manifests,
host-specific distribution, or version-lock distribution needs justify it.
Console-script packaging for a bare `orro` executable points at
`orro.__main__:main` and must remain an alias layer over the witnessd-hosted ORRO
surface.

## Runtime dependency rule

Runtime deps are Python **stdlib + the `openssl` CLI only**. Never add a
third-party runtime dependency to witnessd core.

Depone may be provisioned or pinned for verification, and tests may import Depone
validators. Shipped witnessd capture/runtime paths must not depend on importing
Depone as a Python package.

## Global workflow rule

ORRO is CLI-first but not IDE-hostile. IDEs are fast human steering surfaces; ORRO
owns the evidence-governed background path:

```text
scout -> flowplan -> proofrun -> proofcheck -> handoff
```

The handoff step is gated by an explicit `proofcheck-verdict.json` written by
`proofcheck --out`. `team-ledger-verdict.json` from proofrun is not enough by
itself. If the proofcheck verdict is missing, unreadable, malformed, or not
`decision: "pass"`, `handoff` / `orro handoff` must fail closed and must not
write `orro-handoff.json`.

Non-trivial runs should use progressive disclosure:

- build `repo-profile.json`,
- build `context-pack.json`,
- write `discovery-notes.md` after every two meaningful read/search actions,
- load skillpack/rule bodies only after frontmatter or path matching,
- create `verification-recipe.json` before implementation when checks exist,
- record `verification-receipt.json` and `mcp-tool-receipt-*.json` when those
  actions occur.

## The Depone contract

`witnessd` emits evidence that must satisfy Depone's contract, which is the source
of truth for capture-manifest / runner-receipt / isolation / DSSE / team-ledger /
verification-recipe / verification-receipt / skillpack-lock / MCP-tool-receipt
schemas and their error codes, plus:

```python
canonical_hash = sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))
```

Rules:

- Do not invent schema fields.
- Contract capability changes land in Depone first, then witnessd consumes them.
- Runtime receipt emission belongs in witnessd; receipt verification belongs in
  Depone.

## Testing / dogfood

From the Moonweave workspace:

```bash
cd depone
python3 -m unittest discover -s tests
cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for script in scripts/revalidate_*.py; do
  PYTHONPATH=../depone python3 "$script"
done
scripts/quickstart_check.sh
```

## Invariants

- Pre-verification user status is `evidence-pending`.
- Worker output is not its own trust verdict; the observer and emitter create the
  evidence that Depone later re-derives.
- Skill text, MCP output, IDE/tmux views, and session transcripts are not
  verdicts by themselves.
- witnessd does not grant A1/A2 final trust by itself.
- Each wave's acceptance bar is a committed fixture plus a revalidator that
  Depone re-derives.
