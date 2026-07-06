# ORRO Conformance

The ORRO Engine Contract v0 is documented in
[`../orro-engine-contract-v0.md`](../orro-engine-contract-v0.md).

Depone is verifier-authoritative for proofcheck semantics. The authoritative
conformance manifest lives in Depone at:

```text
docs/orro-conformance/manifest.json
```

witnessd mirrors the same fixture classes through runtime tests:

| Fixture class | witnessd expectation |
| --- | --- |
| `valid-team-ledger-run` | proofrun emits runtime artifacts; proofcheck delegates to Depone. |
| `scout-only` | next/report/auto do not claim ready or complete. |
| `workflow-plan-only` | wrapper context alone is not proof. |
| `wrapper-artifacts-only` | role/auto/report/handoff context alone is not proof. |
| `stale-proofcheck-verdict` | handoff and continuation block. |

This conformance layer exists to prevent drift between the two engine repos.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```
