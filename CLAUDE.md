# witnessd — Agent Context

`witnessd` is the executing runtime engine in the Moonweave pair. It spawns
workers, owns durable sessions, creates worktrees, retries, supervises teams, and
emits observer-signed evidence. Depone is the non-executing verifier that
re-derives the verdict from those bytes.

```text
Depone verifies; witnessd executes; Moonweave Superflow exposes the workflow.
```

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
| `superflow` | flagship goal -> plan -> run -> evidence -> verifier summary |
| `flowplan` | plan-only workflow design |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `superflow auto` | later resume/continuation loop behind evidence gates |
| `superflow ultra` | future high-autonomy profile with stricter gates |

`witnessd` is the engine name, not the main session skill name.

## Runtime dependency rule

Runtime deps are Python **stdlib + the `openssl` CLI only**. Never add a
third-party runtime dependency to witnessd core.

Depone may be provisioned or pinned for verification, and tests may import Depone
validators. Shipped witnessd capture/runtime paths must not depend on importing
Depone as a Python package.

## The Depone contract

`witnessd` emits evidence that must satisfy Depone's contract, which is the source
of truth for capture-manifest / runner-receipt / isolation / DSSE / team-ledger
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
- witnessd does not grant A1/A2 final trust by itself.
- Each wave's acceptance bar is a committed fixture plus a revalidator that
  Depone re-derives.
