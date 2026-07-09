# witnessd — Agent Context (READ FIRST)

`witnessd` is an **executing** team/agent orchestration runtime: it spawns
workers, owns durable sessions, creates worktrees, retries — and every action it
runs **emits observer-signed evidence** that an independent, **non-executing**
verifier (**Depone**, a separate repo: github.com/Moonweave-Systems/Depone)
re-derives A0/A1/A2 assurance from. The one-line thesis: *done is defined by
observer-signed bytes, not by a self-reported "VERIFIED" string.*

- **Source of truth:** `SPEC.md` (full design), `SPEC3.md` (endgame), and
  `docs/orro-productization-roadmap.md` for ORRO wrapper/distribution strategy.
  Wave plans: `docs/plans/`.
  Waves W1→W5 are implemented in committed fixtures: evidence substrate,
  supervised liveness/durable sessions, team fan-in, adapter routing/cost
  controls, and autonomy safety. Depone re-derives the wave claims from
  `scripts/revalidate_w1.py` through `scripts/revalidate_w5.py`.
- **Runtime deps:** Python **stdlib + the `openssl` CLI only**. Never add a
  third-party package. `depone` is a **dev/test-only** dependency (to run
  conformance), never a runtime import of the shipped runtime.
- **Product surface:** ORRO is exposed through `python3 -m witnessd orro ...`
  in this phase. A standalone ORRO repo is deferred until distribution
  packaging, version locks, examples, or marketplace manifests need it. It must
  not contain engine logic.

## The Depone contract (do not drift)

`witnessd` emits evidence that must satisfy **Depone's** contract, which is the
**source of truth**: the capture-manifest / runner-receipt / isolation / DSSE /
team-ledger schemas and their error codes live in `depone/agent_fabric/*` and
`depone/verify/*`, plus
`canonical_hash = sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8"))`.

- **Do NOT invent schema fields.** Match Depone exactly or the evidence fails
  re-derivation.
- witnessd targets **Depone `main`** (which includes the observer-provenance
  contract as of 2026-07-01).
- **Need a new contract capability** (e.g. a new `runner_kind`, a `lane_kind`)?
  It is a **Depone PR first** → then witnessd uses it. Never depend on an
  unmerged/local-only Depone change; that hides drift until CI.

## Testing / dogfood (needs Depone importable, dev-only)

Depone is not a runtime dep, but the tests re-derive verdicts through the real
Depone validators, so Depone must be on `PYTHONPATH` for tests:

```
# from the moonweave workspace (preferred):
make test        # full suite against pinned depone
make dogfood     # emit evidence -> depone re-derives A1/A2

# standalone:
PYTHONPATH=/path/to/depone uv run python3 -m unittest discover -s tests
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_wN.py
```

## Invariants

- **evidence-pending is a hard rule.** No user-facing output may print
  `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE` alone. All status goes
  through the single `render_status()` enum (see `witnessd/status.py`) — this is
  the whole point of the product; do not weaken it for UX.
- **worker cannot seal/validate its own success**; the observer + emitter do.
- **ORRO is not a third engine.** Depone verifies; witnessd executes; ORRO
  exposes the workflow.
- Each wave's Acceptance Bar = a committed fixture + `revalidate_wN.py` that
  Depone re-derives. Land a wave only when that is green.
