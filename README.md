# witnessd

> Done is signed bytes, not a self-reported string.

`witnessd` is the executing half of Moonweave's evidence runtime. It runs
lanes, teams, adapters, retries, worktrees, pause/kill controls, and learning
promotion, then emits observer-captured evidence for a separate verifier to
re-derive. The verifier is
[Depone](https://github.com/Moonweave-Systems/Depone): non-executing,
offline, and limited to verdicts it can derive from bytes.

## Why

Agent runtimes can print success strings that are not evidence. `witnessd`
exists to avoid treating a transcript tag, task-state update, or green local
doctor message as completion. The design source of truth is
[`SPEC.md`](SPEC.md): the runtime may execute aggressively, but final assurance
belongs to the verifier and is capped at A2.

The anti-pattern is documented in code and fixtures:

- status wording is constrained by [`witnessd/status.py`](witnessd/status.py)
  and tested by [`tests/test_status.py`](tests/test_status.py).
- CLI output stays `evidence-pending` until external verification; see
  [`tests/test_cli.py`](tests/test_cli.py) and
  [`tests/test_runtime_depone_decoupling.py`](tests/test_runtime_depone_decoupling.py).
- a green self-report false-positive is demonstrated without using it as a
  trust root in [`scripts/demo_zombie_falsepositive.py`](scripts/demo_zombie_falsepositive.py).

## How

The workspace has two independent repos, described in
[`../CLAUDE.md`](../CLAUDE.md):

```text
witnessd  -> executes lanes and emits signed evidence
Depone    -> does not execute lanes; re-derives verdicts from evidence bytes
```

The evidence flow is:

```text
lane run
  -> observer capture
  -> capture manifest
  -> runner/worktree/team receipts
  -> DSSE/in-toto evidence bundle
  -> Depone offline re-derivation
```

The core implementation paths are
[`witnessd/emitter.py`](witnessd/emitter.py),
[`witnessd/capture.py`](witnessd/capture.py),
[`witnessd/substrate.py`](witnessd/substrate.py),
[`witnessd/signing.py`](witnessd/signing.py),
[`witnessd/fanin.py`](witnessd/fanin.py), and
[`witnessd/team_ledger.py`](witnessd/team_ledger.py). Committed examples live
under [`fixtures/`](fixtures/). Negative fixtures such as
[`fixtures/w1/negative/forged_a3.json`](fixtures/w1/negative/forged_a3.json),
[`fixtures/w1/negative/observer_capture_hash_mismatch.json`](fixtures/w1/negative/observer_capture_hash_mismatch.json),
and [`fixtures/w7/negative/ledger-budget-blocked.json`](fixtures/w7/negative/ledger-budget-blocked.json)
show forged or blocked evidence failing re-derivation.

## Reproduce The Baseline

From this repo:

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_w1.py
```

That revalidates the committed W1 n=1 shell-lane evidence across the contract
surfaces used by Depone: observer separation, capture manifest, capture chain,
operator signature, evidence bundle ingest, runner receipt, trusted-observer
provenance, and evidence-contract binding. On this host the A2 fixture is
explicitly demonstration-only; see
[`fixtures/w1/A2-DEMONSTRATION.md`](fixtures/w1/A2-DEMONSTRATION.md).

To run the full local matrix:

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for script in scripts/revalidate_*.py; do
  PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 "$script"
done
uv run python3 -m witnessd self-test --all
```

From the co-dev workspace:

```bash
cd /home/ubuntu/moonweave
make dogfood
make test
```

## OVERT

[`docs/conformance/OVERT.md`](docs/conformance/OVERT.md) documents the
self-declared OVERT 1.1 alignment: AAL-3, Agentic scope, with exclusions.
[`docs/conformance/witnessd-protocol-profile.md`](docs/conformance/witnessd-protocol-profile.md)
describes the local protocol profile.

Important limit: `evidence_mode` is self-declared in W8/W9. witnessd does not
have a live notary co-signature, co-epoch anchor, transparency log timestamp, or
independent timestamp authority that can prove `contemporaneous` versus
`post_hoc` from bytes alone. OVERT `DELAYED_NOTARY` is not modeled.

## Honest Limits

- A2 is supported by contract fixtures, but this host's W1 A2 example is a
  demonstration fixture, not a real uid-isolated run:
  [`fixtures/w1/A2-DEMONSTRATION.md`](fixtures/w1/A2-DEMONSTRATION.md).
- Assurance is capped at A2. Operator signatures add report-level provenance;
  they do not raise assurance. See [`SPEC.md`](SPEC.md).
- There is no RFC 6962 transparency log, independent IAP notary, or public
  timestamping service in v1.0. See
  [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md).
- Sigstore/Fulcio/Rekor keyless signing is blocked until the production gate
  evidence exists. See
  [`docs/ops/operator-key-rotation.md`](docs/ops/operator-key-rotation.md) and
  [`scripts/revalidate_key_rotation.py`](scripts/revalidate_key_rotation.py).
- Depone is a dev/test verifier dependency for local revalidation. Runtime
  code remains Python stdlib plus the `openssl` CLI; see
  [`tests/test_runtime_depone_decoupling.py`](tests/test_runtime_depone_decoupling.py).

## Design Documents

- [`SPEC.md`](SPEC.md) - design source of truth
- [`docs/plans/`](docs/plans/) - W1-W9 implementation plans
- [`docs/ops/operator-key-rotation.md`](docs/ops/operator-key-rotation.md) -
  operator-key rotation and production gate notes
- [`docs/conformance/`](docs/conformance/) - OVERT and local protocol profile
