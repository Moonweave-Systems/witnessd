# OVERT 1.1 Conformance Statement

## Claim

`witnessd` + Depone align with OVERT 1.1 at **AAL-3** for **Agentic** scope.
This is a schema and documentation alignment statement, not a certification.
`witnessd` acts as the arbiter/emitter; Depone acts as the non-executing
operator-controlled verifier/notary. The maximum grade is AAL-3 because the
notary path is operator-controlled and there is no independent IAP or
transparency log.

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
| RES-5 failure-mode declaration | Reconstruction is represented by `evidence_mode: "post_hoc"`, mapped to OVERT `RECONSTRUCTED`; contemporaneous evidence stays `evidence_mode: "contemporaneous"`. | `tests/test_overt_fields.py`, `fixtures/w8/negative/post_hoc_marked_contemporaneous.json`, `scripts/revalidate_w8.py` |

## OVERT Field Mapping

| OVERT concept | witnessd field | Notes |
| --- | --- | --- |
| Receipt temporality flags | `evidence_mode` | `contemporaneous` maps to flags `0x00`; `post_hoc` maps to OVERT 1.1 `RECONSTRUCTED` (`0x02`). |
| Co-epoch duration | `epoch_seconds` | Default is 300 seconds. This is an operator clock interval, not independent timestamp authority. |
| Receipt monotonic counter | `monotonic_counter` | Positive run-local counter emitted with the capture manifest and signed bundle. |
| Cross-boundary parent reference | `parent_attestation_id` | Optional 64-character lowercase SHA-256 hex reference. Only the hash crosses the boundary. |

## Exclusions

| Exclusion | Status | Architecture reason |
| --- | --- | --- |
| ATT-4 transparency log | Excluded | witnessd does not operate RFC 6962 inclusion/consistency infrastructure. |
| ATT-5 independent IAP notary | Excluded | Depone is non-executing but operator-run in this workspace. |
| MEASURE domain | Excluded | witnessd/Depone perform deterministic per-action verification and make no sampling or statistical safety claim. |
| Agentic-Extended CAS/PoP | Excluded | No capability artifact service or proof-of-possession layer is implemented in this wave. |
| RES-3 break-glass | Excluded | No emergency override artifact or review scheduler is implemented in this wave. |
| HTTP cross-boundary header binding | Excluded | W8 records `parent_attestation_id` at schema level only. |

## Roadmap

A future AAL-4 path would require an independent IAP, transparency log
inclusion/consistency proofs, independent timestamping, registered profile test
vectors, and public verification of the receipt chain. That work is outside the
Solo 1.0 / W8 scope.
