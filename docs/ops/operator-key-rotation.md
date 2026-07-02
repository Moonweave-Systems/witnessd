# Operator Key Rotation Runbook

This runbook defines the W1-W5 production gate in SPEC section 8.2-3. The
committed archive contains a local canary for revalidation, but it does not close
the production-deployment gate by itself. It does not introduce keyless signing
and does not change the A0/A1/A2 assurance ladder.
Operator-key DSSE remains a report-level trust axis with
`keyless_identity=false` and `transparency_logged=false`.

## Policy

- Generate a new Ed25519 operator key before the first `external-team-pilot`
  deployment.
- Rotate operator keys at least every 90 days, and immediately on suspected
  private-key exposure.
- Keep private keys only on the signing host or managed secret store with
  operator ownership and `0600` file permissions when stored as a file.
- Never commit private keys, write them into evidence directories, or pass them
  to Depone verification commands.
- Distribute public keys to Depone out-of-band and configure verification with
  `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`.
- Archive every retired public key so historical evidence remains
  re-verifiable. Do not re-sign old evidence during normal rotation.
- If a private key is compromised, mark that key `compromised`, record the
  compromise time, rotate immediately, and reject evidence signed after the
  compromise time by that key. Evidence signed before the compromise time stays
  verifiable only as archived historical evidence with an explicit compromised
  key note.

## Rotation Procedure

1. Generate a new keypair on the signing host:
   `openssl genpkey -algorithm Ed25519 -out operator-ed25519.pem`
2. Set private key permissions:
   `chmod 0600 operator-ed25519.pem`
3. Derive the public key:
   `openssl pkey -in operator-ed25519.pem -pubout -out operator-ed25519.pub.pem`
4. Add the public key to the out-of-band trusted-key archive.
5. Configure new witnessd emissions to use a new non-secret `key_id`.
6. Configure Depone with the new public key path.
7. Emit one canary evidence bundle and verify it with Depone.
8. Mark the previous key as `retired` after the canary passes.
9. Run `python3 scripts/revalidate_key_rotation.py`.

## Keyless Gate

Sigstore Fulcio/Rekor keyless signing remains blocked until this runbook has
been exercised in at least one `external-team-pilot` deployment and the
resulting archive/canary evidence is committed or otherwise durably retained.
Local dogfood, local canaries, hand-authored fixtures, and CI-only runs do not
count as production deployments for this gate.

For this gate, `external-team-pilot` means a named team run outside local-only
developer dogfood, executed with the deployed witnessd runtime, where Depone can
re-derive the evidence from persisted bytes. Opening the gate requires all of
the following evidence records:

1. `deployment_record`: deployment id, operator, team scope, start/end
   timestamps, and witnessd git SHA.
2. `rotated_key_archive`: retired-to-current key continuity, public-key paths,
   and the archive produced by this runbook.
3. `canary_bundle`: current-key canary with
   `source_kind == "operator-key-rotation-canary"` that passes
   `scripts/revalidate_key_rotation.py`.
4. `depone_verification`: Depone verification transcript for the production
   deployment bundle and canary bundle.
5. `operator_review`: human operator review that the run was not local-only
   dogfood and that private keys were not committed or exposed.

The committed `fixtures/key-rotation/operator-key-archive.json` records
`production_gate.status = "blocked"` and the required evidence entries as
`missing` until that deployment evidence exists. The revalidation script rejects
an `open` gate unless every required evidence entry is recorded with a stable
repo-relative artifact path and matching SHA-256 hash. Recorded evidence paths
must be unique, and each artifact must have the expected JSON shape:

- `deployment_record`: `kind =
  "witnessd-external-team-pilot-deployment"`, `rollout_stage =
  "external-team-pilot"`, deployment id/operator/team scope/timestamps,
  witnessd git SHA, `deployed_runtime = true`, `local_dogfood = false`, and
  `ci_only = false`.
- `rotated_key_archive`: `kind =
  "witnessd-operator-key-rotation-record"`, retired/current key ids,
  `rotated_to` linking to the current runtime key id, and the current-key canary
  bundle path.
- `canary_bundle`: a signed Depone evidence bundle whose predicate
  `source_kind` is `operator-key-rotation-canary` and whose single signature
  key id matches the current witnessd runtime key id.
- `depone_verification`: `kind = "depone-verification-transcript"`,
  `verifier = "depone"`, `all_passed = true`, and passing
  `production_bundle` plus `canary_bundle` results.
- `operator_review`: `kind = "witnessd-operator-review"`, review timestamp,
  `decision = "approve-keyless-gate"`, `local_dogfood = false`, and no committed
  or exposed private keys.
