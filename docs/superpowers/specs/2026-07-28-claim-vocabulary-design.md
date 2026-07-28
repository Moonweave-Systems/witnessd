# Claim Vocabulary Consolidation Design

## Goal

Represent operator-facing declaration claims with one internal seven-axis model
while preserving every existing JSON field, value, decision, and blocking or
advisory outcome.

## Internal model

`witnessd.claim` owns the finite vocabularies for `source`, `observation`,
`integrity`, `evaluation`, `effect`, and `freshness`, plus an immutable `Claim`
that also carries `value`.

The public construction paths are deliberately asymmetric:

- `Claim.from_producer(...)` accepts no evaluation argument and always records
  `unevaluated`.
- `Claim.from_verifier(...)` requires a verifier evaluation and always records
  `source=verifier`.

The `Claim` invariant also rejects a non-verifier source paired with any
evaluation other than `unevaluated`, including direct construction. This keeps
the producer restriction structural at both the supported API and runtime
boundary.

## Compatibility projections

The four producer declaration builders construct a `Claim` and immediately
project it back to their existing artifact dictionaries:

- model declaration
- write-scope declaration
- skill-routing declaration
- tool declaration

Projection functions preserve the current schema versions, keys, and values.
Legacy words such as `verified`, `requested-unverified`, `pass`, `fail`, and
`advisory-fail` remain compatibility outputs derived from the claim axes and
claim value; they are not stored as claim evaluations.

The ORRO report declaration summary reconstructs a producer claim from each
stored declaration and renders the same summary dictionary. It continues to
quarantine all four declaration types from Depone-derived results.

## Scope

This pass converts only the four producer declarations and the report surface
that summarizes them. Other internal status, continuation, roadmap, and
verdict structures remain unchanged because converting them would broaden the
refactor and risk public behavior without improving the declaration collision
targeted here.

Depone code and verifier contracts are unchanged.

## Validation

Tests first establish:

- every vocabulary rejects unknown values;
- producer construction has no evaluation parameter;
- invalid source/evaluation combinations fail;
- verifier construction is the only supported path to a non-unevaluated claim;
- all four legacy declaration payloads remain byte-for-byte compatible at the
  dictionary level;
- report declaration summaries retain their current fields and values.

Integration evidence compares `status`, `handoff`, and `proofcheck` JSON from
two genuine shell-reference ORRO flows over clones of the same seed repository,
normalizing only run-specific absolute paths and cryptographic or timestamp
material. Field paths and decision-bearing values must have no diff.
