# witnessd OVERT Protocol Profile

## Scope

This profile documents the witnessd/Depone evidence encoding used for W8 OVERT
1.1 schema alignment. It is a self-declared implementation profile for local
AAL-3 operation. It does not claim registered OVERT Protocol Profile 1.0
conformance.

## Canonical Hash

All witnessd and Depone canonical object hashes use this exact byte procedure:

```python
sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))
```

This intentionally preserves the existing Depone contract. W8 does not switch
to JCS, CBOR, or OVERT Protocol Profile 1.0 BLS threshold receipts.

## Signature and Envelope

| Surface | Profile choice |
| --- | --- |
| Envelope | in-toto Statement v1 wrapped in DSSE |
| Signature | Ed25519 via the local `openssl` CLI |
| Runtime dependency | Python stdlib plus `openssl` CLI only |
| Trust root | Operator public key distributed out-of-band |
| Transparency log | Not implemented |
| Notary independence | Operator-controlled Depone verifier |

Key rotation and operator-key handling are documented in
[`docs/ops/operator-key-rotation.md`](../ops/operator-key-rotation.md).

## Field Mapping

| witnessd artifact | OVERT role |
| --- | --- |
| `capture-manifest.json` | Arbiter-side evidence manifest and local attestation subject |
| `runner-receipt.json` | Runner action receipt bound into the signed bundle |
| `bundle.json` | DSSE/in-toto evidence bundle for Depone ingest |
| `provenance.json` | Trusted-observer provenance over the capture manifest |
| `runlog.jsonl` | append-only emitter/source-of-truth events |

| witnessd field | OVERT field/concept | Encoding |
| --- | --- | --- |
| `evidence_mode: "contemporaneous"` | receipt flags `0x00` | self-declared string enum |
| `evidence_mode: "post_hoc"` | reconstructed receipt class | self-declared string enum |
| `epoch_seconds` | co-epoch duration | positive integer, default `300` |
| `monotonic_counter` | receipt monotonic counter | positive integer |
| `parent_attestation_id` | cross-boundary parent reference | optional 64-character lowercase SHA-256 hex |

`epoch_seconds` is based on the operator clock and does not represent an
independent timestamp authority. `parent_attestation_id` is content-free: only
the hash reference crosses the boundary.

`evidence_mode` is not enforced by bytes in W8/W9. witnessd has no live notary
co-signature, co-epoch anchor, transparency-log timestamp, or independent
timestamp authority that can prove `contemporaneous` versus `post_hoc`.
`DELAYED_NOTARY` (`0x01`) is not modeled.

## Validation

W8 validation is performed by:

```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest tests.test_overt_fields -v
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_w8.py
```

The full workspace matrix also re-runs W1-W5 and key rotation revalidation
against the pinned Depone checkout.

The v2 one-command team demo is re-derived from committed bytes by:

```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_v2_demo.py
```

That script recomputes the sealed plan hash and dispatch events, verifies each
Codex lane capture manifest, runner receipt, signed bundle, and team-ledger
verdict, and includes a forged-signature negative check. It does not launch
agents or require the Codex subscription session used for the original run.
