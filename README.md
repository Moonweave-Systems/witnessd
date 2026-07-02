# witnessd

> **Done is signed bytes, not a self-reported string.**

`witnessd` is the executing half of Moonweave: it spawns and supervises
agent/team work, captures what happened, signs the resulting evidence, and leaves
bytes that another program can re-check later. The point is not to trust a model,
a runner, or a terminal string that says "verified". The point is to make the
claim falsifiable from committed evidence.

## Why this exists

Most agent orchestrators ask the same system that performed the work to grade the
work. That makes completion a self-reporting problem. `witnessd` instead treats
completion as an evidence problem:

1. run or supervise the work,
2. capture manifests, receipts, runlog events, and signatures,
3. hand those bytes to a separate verifier,
4. accept only what can be re-derived from the bytes.

The design source of truth is [`SPEC.md`](SPEC.md); wave plans and review notes
live in [`docs/plans/`](docs/plans/).

## Architecture

Moonweave is two independent repositories, not a monorepo:

```text
moonweave/
├── witnessd/   runtime: executes teams/agents and emits signed evidence
└── depone/     verifier: non-executing; re-derives A0/A1/A2 from evidence bytes
```

`witnessd` must not invent verifier schema fields. The evidence contract is owned
by Depone; see the workspace rules in [`CLAUDE.md`](CLAUDE.md) and the protocol
profile in [`docs/conformance/witnessd-protocol-profile.md`](docs/conformance/witnessd-protocol-profile.md).

The evidence flow is:

```text
agent/team work
  -> witnessd capture manifest + runner receipt + runlog
  -> operator-key DSSE bundle
  -> committed fixture bytes
  -> Depone offline re-derivation
```

Negative fixtures are part of the contract: tampered or mismatched bytes are
expected to fail revalidation instead of being upgraded by runtime assertion.
Start with [`fixtures/w1/`](fixtures/w1/) and [`scripts/revalidate_w1.py`](scripts/revalidate_w1.py).

## What v1.0 demonstrates

- W1 evidence substrate: fixtures under [`fixtures/w1/`](fixtures/w1/) are
  re-derived by [`scripts/revalidate_w1.py`](scripts/revalidate_w1.py).
- W2 supervised liveness / durable sessions:
  [`scripts/revalidate_w2.py`](scripts/revalidate_w2.py).
- W3 team fan-in and conflict evidence:
  [`scripts/revalidate_w3.py`](scripts/revalidate_w3.py) and
  [`scripts/demo_w3_team_conflict.py`](scripts/demo_w3_team_conflict.py).
- W4 adapter routing and cost controls:
  [`scripts/revalidate_w4.py`](scripts/revalidate_w4.py) and
  [`scripts/demo_w4.py`](scripts/demo_w4.py).
- W5 pause / kill / resume safety:
  [`scripts/revalidate_w5.py`](scripts/revalidate_w5.py).
- W7 team adapter wiring:
  [`scripts/revalidate_w7.py`](scripts/revalidate_w7.py).
- W8 OVERT field alignment:
  [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md),
  [`fixtures/w8/`](fixtures/w8/), and [`scripts/revalidate_w8.py`](scripts/revalidate_w8.py).

The runtime dependency target is intentionally small: Python standard library plus
the `openssl` CLI. Depone is a development/test verifier dependency, not a
runtime dependency of `witnessd`.

## Reproduce the core proof

From the Moonweave workspace with Depone checked out next to `witnessd`:

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in scripts/revalidate_*.py; do
  PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 "$s"
done
uv run python3 -m witnessd self-test --all
```

For the W1 n=1 proof directly:

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_w1.py
```

For a depone-free runtime smoke test, run in an environment where Depone is not on
`PYTHONPATH`:

```bash
cd /home/ubuntu/moonweave/witnessd
python3 -c "import witnessd.emitter, witnessd.__main__"
python3 -m witnessd self-test --all
```

## OVERT and assurance ceiling

`witnessd` + Depone document schema-level OVERT 1.1 alignment at **AAL-3
Agentic** in [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md). This is a
conformance statement, not an external certification.

Honest exclusions matter:

- A2 requires a uid-isolated host path; [`fixtures/w1/A2-DEMONSTRATION.md`](fixtures/w1/A2-DEMONSTRATION.md)
  records the current demonstration limit.
- There is no independent transparency log or independent IAP/notary in v1.0.
- W8 `evidence_mode` is self-declared model data, not proof of live notary
  co-signing or co-epoch anchoring.
- OVERT `DELAYED_NOTARY` (`0x01`) is not modeled in v1.0.
- Keyless signing is a separate blocked gate, tracked in
  [`docs/plans/2026-07-02-w6-keyless-signing.md`](docs/plans/2026-07-02-w6-keyless-signing.md).

## Release validation matrix

Before cutting `v1.0.0`, collect local evidence for each row:

| Gate | Command | Expected result |
| --- | --- | --- |
| Unit suite | `PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests` | all tests pass |
| Revalidators | `for s in scripts/revalidate_*.py; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 "$s"; done` | every revalidator exits 0 |
| Self-test | `uv run python3 -m witnessd self-test --all` | exits 0 without standalone `VERIFIED` status output |
| Cwd independence | `uv run python3 -m unittest discover -s /home/ubuntu/moonweave/witnessd/tests -t /home/ubuntu/moonweave/witnessd` from outside the repo | all tests pass |
| Runtime decoupling | `python3 -c "import witnessd.emitter, witnessd.__main__"` without Depone on `PYTHONPATH` | import succeeds |
| Workspace dogfood | `make dogfood && make test` from `/home/ubuntu/moonweave` when available | witnessd emits; Depone re-derives |

CI for these gates belongs in this repository. Depone CI changes belong in the
separate Depone repository.

## v1.0.0 tag message draft

```text
v1.0.0: signed-bytes runtime release

W1-W8 summary:
- W1 evidence substrate with Depone re-derivation fixtures.
- W2 supervised liveness and durable session evidence.
- W3 team fan-in / conflict evidence.
- W4 adapter routing and cost controls.
- W5 pause, kill, and resume safety gates.
- W7 team adapter wiring.
- W8 OVERT field alignment and evidence_mode honesty notes.

Conformance:
- witnessd executes and emits evidence.
- Depone remains non-executing and re-derives A0/A1/A2 from bytes.
- OVERT 1.1 alignment is documented as AAL-3 Agentic, not certification.

Known limits:
- A2 remains demonstration-only without uid-isolated host proof.
- No independent transparency log / IAP notary in v1.0.
- Keyless signing remains blocked outside this release gate.
```

Do not push the tag without explicit operator approval.

## Further reading

- [`SPEC.md`](SPEC.md) — design source of truth.
- [`docs/plans/`](docs/plans/) — implementation waves.
- [`docs/ops/operator-key-rotation.md`](docs/ops/operator-key-rotation.md) — operator key rotation.
- [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md) — OVERT mapping and exclusions.
