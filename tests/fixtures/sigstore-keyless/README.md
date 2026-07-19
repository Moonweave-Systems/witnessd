# Real Sigstore conformance fixture

These bytes were copied from the approved increment-2 conformance spike. The
bundle is the public-good Sigstore v0.3 attestation for the published
`sigstore-4.4.0` wheel. `identity-policy.json` pins the certificate identity and
`prod-trusted-root.json` is the matching production trusted root used by
Depone's offline verifier.

The fixture is never presented as a witnessd live-sign result. Its purpose is
to prove that the witnessd adapter/substrate sidecar is handed unchanged to
Depone and that Depone re-derives the real bundle as
`keyless-transparency-logged` over the exact wheel bytes.
