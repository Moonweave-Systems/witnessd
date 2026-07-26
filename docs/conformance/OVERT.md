# OVERT 1.1 Conformance Statement

## Claim

`witnessd` + Depone align with OVERT 1.1 at **AAL-3** for **Agentic** scope.
This is a schema and documentation alignment statement, not a certification.
`witnessd` acts as the arbiter/emitter; Depone acts as the non-executing,
operator-controlled verifier. The AAL-3 ceiling is unchanged by optional
Sigstore anchoring.

The current implementation is witnessd 2.31.1 with Depone 0.2.11. The
evidence substrate still caps local observation assurance at A2. The committed
W12 evidence records the host setup required for new A2 evidence: a dedicated
observer uid, an observer-owned `0700` directory, and a runner uid that cannot
write that directory.

OVERT source checked for this statement:

- `https://overt.is/latest.md` (OVERT 1.1, Sections 4.1, 4.1.1, 4.2, and 4.7.2)
- `https://overt.is/OVERT_1.1_Foundations.pdf`
- `https://overt.is/OVERT_1.1_Annexes.pdf`

OVERT 1.1 Section 4.1 distinguishes policy documentation (AAL-1), process
records (AAL-2), automated monitoring (AAL-3), and independent cryptographic
attestation (AAL-4). AAL-4 requires an Independent Attestation Provider (IAP)
structurally independent of the AI system operator and producing tamper-evident
proof of control execution. Section 4.7.2 also requires at least two
independent transparency-log monitors for an AAL-4 deployment. The standard's
architecture obtains inclusion and consistency proofs from an append-only log;
it does not require the subject system to operate that log.

| OVERT control | witnessd/Depone mapping | Evidence |
| --- | --- | --- |
| PRO-1 boundary arbiter and permit/deny receipt | `witnessd` emits capture manifests, runner receipts, bundles, and runlog events; Depone re-derives from bytes. | `witnessd/emitter.py`, `witnessd/substrate.py`, `tests/test_emitter.py`, `scripts/revalidate_w1.py` |
| ATT-1 content non-egress | Evidence binds hashes and metadata; protected content is not required for Depone ingest. | `witnessd/capture.py`, `witnessd/substrate.py`, `fixtures/w1/`, `fixtures/w8/` |
| ATT-3 three-phase shape | Local observed capture, operator-key DSSE bundle, and Depone asynchronous re-derivation match the provisional-to-final shape, capped at AAL-3. | `witnessd/emitter.py`, `witnessd/signing.py`, `scripts/revalidate_w8.py` |
| RES-1 crypto-gated control loop | W5 pause/kill/resume gates derive from signed runlog evidence and fail closed when evidence is missing. | `witnessd/pause.py`, `witnessd/killswitch.py`, `scripts/revalidate_w5.py` |
| RES-5 failure-mode declaration | Reconstruction is self-declared as `evidence_mode: "post_hoc"`, mapped to OVERT `RECONSTRUCTED`; contemporaneous evidence is self-declared as `evidence_mode: "contemporaneous"`. witnessd does not byte-prove temporality. | `tests/test_overt_fields.py`, `fixtures/w8/negative/post_hoc_marked_contemporaneous.json`, `scripts/revalidate_w8.py` |
| ATT-4 transparency-log inclusion | **Partial.** Opt-in `--keyless` can obtain a real Sigstore keyless attestation containing Fulcio identity material and a Rekor entry, giving inclusion in an independently operated transparency log as an additive sidecar. It does not make every receipt logged: keyless is opt-in per run, and this implementation has not engaged the two independent transparency-log monitors required by OVERT §4.7.2. | `witnessd/__main__.py`, `witnessd/adapters/sigstore_keyless.py`, `witnessd/adapters/_keyless_sign_helper.py`, `witnessd/substrate.py`, `tests/test_substrate_keyless_guard.py`, `tests/test_sigstore_keyless.py`, `tests/fixtures/sigstore-keyless/real-bundle.json`, `docs/keyless-live-smoke.md` |
| Role-capability policy | **Partial.** In addition to write-scope, skill-routing declarations and observed skills are bound into evidence; Depone re-derives the skill-routing and policy-conformance rollup. This is policy conformance evidence, not independent control-execution attestation. | `witnessd/role_capability.py`, `witnessd/substrate.py`, `witnessd/cli/verify.py`, `tests/test_adapter_run.py`, `tests/test_role_capability.py` |
| Code health monitoring | **Partial.** `code_health` records configured gates with `block` or `advisory` enforcement. Depone rolls up gate results; advisory failures do not block handoff, while blocking failures do. This is repository-health telemetry, not a claim of design quality. | `witnessd/health_detect.py`, `witnessd/cli/verify.py`, `orro/skillpacks/code-health.md`, `tests/test_cli_verify_health.py`, `tests/test_orro_check.py` |
| Evidence substrate binding for health gates | **Partial.** The witnessd 2.31.0 / Depone 0.2.11 substrate (retained in current witnessd 2.31.1) makes the health-gate digest manifest the named `health-gate-artifacts.json` subject in the signed bundle. The verifier checks that binding before re-deriving health exit-code results; evidence it cannot authenticate cannot block handoff. This makes the telemetry binding auditable but does not change the AAL grade. | `witnessd/substrate.py`, `witnessd/cli/verify.py`, `tests/test_substrate.py`, `tests/test_orro_check.py`, `Depone: depone/verify/evidence_contract.py`, `Depone: tests/test_code_health_contract.py` |

## OVERT Field Mapping

| OVERT concept | witnessd field | Notes |
| --- | --- | --- |
| Receipt temporality flags | `evidence_mode` | Self-declared only. `contemporaneous` maps to flags `0x00`; `post_hoc` maps to OVERT 1.1 `RECONSTRUCTED` (`0x02`). witnessd does not model OVERT `DELAYED_NOTARY` (`0x01`). |
| Co-epoch duration | `epoch_seconds` | Default is 300 seconds. This is an operator clock interval, not independent timestamp authority. |
| Receipt monotonic counter | `monotonic_counter` | Positive run-local counter emitted with the capture manifest and signed bundle. |
| Cross-boundary parent reference | `parent_attestation_id` | Optional 64-character lowercase SHA-256 hex reference. Only the hash crosses the boundary. |
| Keyless transparency anchor | `keyless_attestation` | Optional Sigstore v0.3 sidecar. Fulcio identity and Rekor inclusion are independently verifiable when the bundle and trust inputs are available; this does not attest control execution or raise AAL/A2. |

## Temporality Honesty

`evidence_mode` is a self-declared, unenforced field in the current witnessd
2.31.1 runtime. The runtime has no independent notary co-signature, co-epoch
anchor, or timestamp authority that can prove `contemporaneous` versus
`post_hoc` from the bytes alone. The W8 negative fixture
`fixtures/w8/negative/post_hoc_marked_contemporaneous.json` documents that a
post-hoc source can be mislabeled as `contemporaneous`; the revalidator keeps
that limitation visible instead of claiming to detect it. OVERT
`DELAYED_NOTARY` (`0x01`) is not modeled.

Keyless anchoring adds transparency and timestamp anchoring for the opt-in
sidecar, but it does not make `evidence_mode` independently enforced and does
not move the AAL grade.

A2 evidence in this repository is host-conditional. `fixtures/w12/` contains
operator-key-signed evidence bytes from a local observer-launched uid boundary:
the observer process runs under the dedicated observer uid, the runner uid is
distinct and non-root, and the observer directory is not writable by the runner.
Other hosts must reproduce that dedicated-observer-uid setup before emitting
new A2 evidence.

## Exclusions and Remaining Gaps

| Exclusion | Status | Architecture reason |
| --- | --- | --- |
| ATT-5 independent IAP notary | Excluded | OVERT AAL-4 requires an IAP structurally independent of the AI system operator and producing tamper-evident proof of control execution. Depone is operator-run, and Sigstore certifies our identity and logs our signature; neither is that independent control-execution attestation. |
| Two independent transparency-log monitors | Excluded | OVERT §4.7.2 requires at least two independent monitors for AAL-4; this repository does not engage them. |
| DELAYED_NOTARY receipt flag | Excluded | witnessd has no live notary co-sign or delayed-notary anchor. |
| MEASURE domain | Excluded | witnessd/Depone perform deterministic per-action verification and make no sampling or statistical safety claim. |
| Agentic-Extended CAS/PoP | Excluded | No capability artifact service or proof-of-possession layer is implemented in this wave. |
| RES-3 break-glass | Excluded | No emergency override artifact or review scheduler is implemented in this wave. |
| HTTP cross-boundary header binding | Excluded | W8 records `parent_attestation_id` at schema level only. |

## Roadmap

The AAL-3 ceiling remains binding. OVERT AAL-4 requires an Independent
Attestation Provider structurally independent of the operator to produce
tamper-evident proof that controls executed. Sigstore's Fulcio certificate
identifies our signer and Rekor logs our signature; it does not attest that our
controls executed. Depone is also operator-run. Therefore keyless anchoring
improves transparency and timestamp anchoring without moving the AAL grade.
An AAL-4 path would additionally need the required independent transparency-log
monitors and any other applicable OVERT profile evidence. No claim of AAL-4 is
made here.
