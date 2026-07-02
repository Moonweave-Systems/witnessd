# Operator Key Rotation Runbook

This runbook defines the W1-W5 production gate in SPEC section 8.2-3. The
committed archive contains a local canary for revalidation, but it does not close
the production-deployment gate by itself. It does not introduce keyless signing
and does not change the A0/A1/A2 assurance ladder.
Operator-key DSSE remains a report-level trust axis with
`keyless_identity=false` and `transparency_logged=false`.

## Policy

- Generate a new Ed25519 operator key before first production team deployment.
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
been exercised in at least one production team deployment and the resulting
archive/canary evidence is committed or otherwise durably retained.

The committed `fixtures/key-rotation/operator-key-archive.json` records
`production_gate.status = "blocked"` until that deployment evidence exists.
