# OVERT 1.1 Conformance Statement

## Claim

`witnessd` + Depone align with OVERT 1.1 at **AAL-3** for **Agentic** scope.
This is a schema and documentation alignment statement, not a certification.
`witnessd` acts as the arbiter/emitter; Depone acts as the non-executing
operator-controlled verifier/notary. The maximum grade is AAL-3 because the
notary path is operator-controlled and there is no independent IAP or
transparency log.

This OVERT statement does not raise Depone's assurance ceiling. A2 remains the
maximum assurance grade. The committed W12 A2 evidence bytes record the local
host setup required for new A2 evidence: a dedicated observer uid, an
observer-owned `0700` directory, and a runner uid that cannot write that
directory.

OVERT source checked for this statement:

- `https://overt.is/OVERT_1.1_Foundations.pdf`
- `https://overt.is/OVERT_1.1_Annexes.pdf`
- `https://overt.is/latest.md`

| OVERT control | witnessd/Depone mapping | Evidence |
| --- | --- | --- |
| PRO-1 boundary arbiter and permit/deny receipt | `witnessd.emitter.emit_lane_evidence` emits capture manifests, runner receipts, bundles, and runlog events; Depone re-derives from bytes. | `witnessd/emitter.py`, `witnessd/substrate.py`, `tests/test_emitter.py`, `scripts/revalidate_w1.py` |
| ATT-1 content non-egress | Evidence binds hashes and metadata; protected content is not required for Depone ingest. | `witnessd/capture.py`, `witnessd/substrate.py`, `fixtures/w1/`, `fixtures/w8/` |
| ATT-3 three-phase shape | Local observed capture, operator-key DSSE bundle, and Depone asynchronous re-derivation match the provisional-to-final shape, capped at AAL-3. | `witnessd/emitter.py`, `witnessd/signing.py`, `scripts/revalidate_w8.py` |
| RES-1 crypto-gated control loop | W5 pause/kill/resume gates derive from signed runlog evidence and fail closed when evidence is missing. | `witnessd/pause.py`, `witnessd/killswitch.py`, `scripts/revalidate_w5.py` |
| RES-5 failure-mode declaration | Reconstruction is self-declared as `evidence_mode: "post_hoc"`, mapped to OVERT `RECONSTRUCTED`; contemporaneous evidence is self-declared as `evidence_mode: "contemporaneous"`. witnessd does not byte-prove temporality. | `tests/test_overt_fields.py`, `fixtures/w8/negative/post_hoc_marked_contemporaneous.json`, `scripts/revalidate_w8.py` |

## OVERT Field Mapping

| OVERT concept | witnessd field | Notes |
| --- | --- | --- |
| Receipt temporality flags | `evidence_mode` | Self-declared only. `contemporaneous` maps to flags `0x00`; `post_hoc` maps to OVERT 1.1 `RECONSTRUCTED` (`0x02`). witnessd does not model OVERT `DELAYED_NOTARY` (`0x01`). |
| Co-epoch duration | `epoch_seconds` | Default is 300 seconds. This is an operator clock interval, not independent timestamp authority. |
| Receipt monotonic counter | `monotonic_counter` | Positive run-local counter emitted with the capture manifest and signed bundle. |
| Cross-boundary parent reference | `parent_attestation_id` | Optional 64-character lowercase SHA-256 hex reference. Only the hash crosses the boundary. |


## Temporality Honesty

`evidence_mode` is a self-declared, unenforced field in witnessd v1.0. The
current runtime has no live notary co-signature, independent co-epoch anchor, or
transparency timestamp that can prove `contemporaneous` versus `post_hoc` from
the bytes alone. The W8 negative fixture
`fixtures/w8/negative/post_hoc_marked_contemporaneous.json` is therefore an
honesty fixture: it documents that a post-hoc source can be mislabeled as
`contemporaneous`; `scripts/revalidate_w8.py` preserves that fact instead of
claiming to detect it. OVERT `DELAYED_NOTARY` (`0x01`) is not modeled.

A2 evidence in this repository is host-conditional. `fixtures/w12/` contains
operator-key-signed evidence bytes from a local observer-launched uid boundary:
the observer process runs under the dedicated observer uid, the runner uid is
distinct and non-root, and the observer directory is not writable by the runner.
Other hosts must reproduce that dedicated-observer-uid setup before emitting new
A2 evidence.

## Exclusions

| Exclusion | Status | Architecture reason |
| --- | --- | --- |
| ATT-4 transparency log | Excluded | witnessd does not operate RFC 6962 inclusion/consistency infrastructure. |
| ATT-5 independent IAP notary | Excluded | Depone is non-executing but operator-run in this workspace. |
| DELAYED_NOTARY receipt flag | Excluded | witnessd has no live notary co-sign or delayed-notary anchor. |
| MEASURE domain | Excluded | witnessd/Depone perform deterministic per-action verification and make no sampling or statistical safety claim. |
| Agentic-Extended CAS/PoP | Excluded | No capability artifact service or proof-of-possession layer is implemented in this wave. |
| RES-3 break-glass | Excluded | No emergency override artifact or review scheduler is implemented in this wave. |
| HTTP cross-boundary header binding | Excluded | W8 records `parent_attestation_id` at schema level only. |

## Temporality Honesty

`evidence_mode` is not cryptographically or independently enforced in W8/W9.
witnessd has no live notary co-signature, co-epoch anchor, transparency-log
timestamp, or independent timestamp authority that can prove
`contemporaneous` versus `post_hoc` from bytes alone. A mislabeled fixture can
document the risk, but witnessd cannot detect the lie without external
temporality evidence. OVERT `DELAYED_NOTARY` (`0x01`) is not modeled.

## Roadmap

No AAL-4 path is implemented in this release. A future path would require an
independent IAP, transparency log inclusion/consistency proofs, independent
timestamping, registered profile test vectors, and public verification of the
receipt chain. That work is outside the v2.0.0 release scope.
