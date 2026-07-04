# witnessd — Agent Context (READ FIRST)

`witnessd` is the **executing runtime engine** in the Moonweave pair. It spawns
workers, owns durable sessions, creates worktrees, retries, supervises teams, and
emits observer-signed evidence. Depone is the non-executing verifier that
re-derives the verdict from those bytes.

## Source of truth

[`SPEC3.md`](SPEC3.md) is the current witnessd × Depone final-form specification
and the only top-level witnessd product/runtime authority. `SPEC.md` and
`SPEC2.md` are earlier foundation specs; `docs/plans/*`, `SKILL.md`, `AGENTS.md`,
README, and fixture notes are derived or wave-specific documents. If they
conflict with `SPEC3.md`, `SPEC3.md` wins.

For the Depone verifier contract itself, Depone's `docs/spec.md` is the
authority.

```text
Depone verifies; witnessd executes; Moonweave exposes the workflow.
```

## Runtime dependency rule

Runtime deps are Python **stdlib + the `openssl` CLI only**. Never add a
third-party runtime dependency to witnessd core.

Depone may be provisioned or pinned for verification, and tests may import Depone
validators. Shipped witnessd capture/runtime paths must not depend on importing
Depone as a Python package.

## The Depone contract (do not drift)

`witnessd` emits evidence that must satisfy **Depone's** contract, which is the
source of truth for capture-manifest / runner-receipt / isolation / DSSE /
team-ledger schemas and their error codes, plus:

```python
canonical_hash = sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))
```

Rules:

- **Do not invent schema fields.** Match Depone exactly or the evidence fails
  re-derivation.
- Contract capability changes land in Depone first, then witnessd consumes them.
- Runtime receipt emission belongs in witnessd; receipt verification belongs in
  Depone.

## User-surface rule

The session-facing skill name is `proofrun`, not `witnessd`. It is powered by
witnessd × Depone. Do not create separate end-user `witnessd` and `Depone` skills
as the main product UX.

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
- Each wave's Acceptance Bar = committed fixture + revalidator that Depone
  re-derives. Land a wave only when that is green.
