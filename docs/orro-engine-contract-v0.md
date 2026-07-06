# ORRO Engine Contract v0

This is witnessd's runtime-side view of the ORRO engine contract. Depone remains
verifier-authoritative for proofcheck semantics.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

## Engine Responsibilities

| Engine/surface | Responsibility | Forbidden |
| --- | --- | --- |
| Depone | Verify persisted evidence bytes and emit verdicts. | Execute workers or trust wrapper prose. |
| witnessd | Execute `proofrun` and team lanes, emit evidence artifacts, delegate `proofcheck` to Depone. | Issue final trust or duplicate Depone verifier logic. |
| ORRO | Expose the workflow and package wrapper context. | Become a third engine or make wrapper artifacts proof. |

The standalone ORRO product/distribution repository lives at
<https://github.com/Moonweave-Systems/ORRO>. It owns product onboarding,
examples, distribution drafts, doctrine, and e2e smoke-contract docs. It does
not redefine witnessd runtime execution truth, does not redefine Depone
verifier truth, and must not duplicate witnessd runtime code.

## Runtime Artifact Classes

| Artifact | Class | witnessd obligation |
| --- | --- | --- |
| `repo-profile.json` | intent/wrapper context | May emit during scout; never treat as execution proof. |
| `context-pack.json` | intent/wrapper context | May emit during scout; never treat as execution proof. |
| `sealed-plan.json` | intent | Runtime plan context only. |
| `workflow-plan.json` | intent | Bind into proofrun when supplied; never count as proof. |
| `workflow-plan-binding.json` | wrapper context | Preserve binding context. |
| `role-lane-plan.json` | intent | Validate before proofrun; executable intent, not proof. |
| `role-lane-plan-binding.json` | wrapper context | Preserve binding context. |
| `workflow-role-dispatch.json` | wrapper context | Map roles to evidence refs; context, not proof. |
| `team-ledger.json` | execution evidence | Emit after proofrun/team execution. |
| `team-ledger-verdict.json` | verifier output | Local candidate verdict; not enough for handoff. |
| `verification-recipe.json` | intent | Depone contract artifact; witnessd must not make it proof. |
| `verification-receipt.json` | execution evidence | Evidence only when valid under Depone contract. |
| `proofcheck-verdict.json` | verifier output | Must be written by explicit proofcheck before handoff. |
| `orro-continuation-decision.json` | wrapper context | Next-action advice, not proof. |
| `orro-auto-plan.json` | wrapper context | Recommendation context, not proof. |
| `orro-auto-receipt.json` | wrapper context | Orchestration metadata, not task success. |
| `orro-auto-session.json` | wrapper context | Orchestration metadata, not task success. |
| `orro-report.json` | wrapper context | Human summary, not proof. |
| `orro-handoff.json` | human review package | Review package, not approval. |
| `orro-engine-lock.json` | readiness/distribution metadata | Version alignment metadata, not proof. |

## Trust Rules

- Workflow plan is intent, not proof.
- Role-lane plan is executable intent, not proof.
- Role dispatch is context, not proof.
- Auto artifacts are orchestration metadata, not proof.
- Report is summary, not proof.
- Handoff is review package, not approval.
- Engine-lock is distribution metadata, not proof.
- Existing proofcheck verdict is not an input trust root.
- Verification recipe is intent.
- Verification receipt is execution evidence only if valid.
- MCP/tool output is observed fact, not trust root.
- Skill text, transcripts, prose, role names, and model confidence never raise
  assurance.

## Required Gates

- Scout-only directories must not proofcheck-pass.
- Proofrun evidence must exist before proofcheck can pass.
- Handoff requires a passing bound `proofcheck-verdict.json`.
- Auto may not bypass proofcheck or handoff gates.
- Report may not upgrade status beyond observed artifacts.

## Compatibility

`Superflow`/`superflow` remains historical compatibility only for fixture paths,
schema aliases, legacy metadata, and compatibility commands. New primary public
naming is ORRO/`orro`.

## Drift Check

Maintainers should run the no-dependency checker when changing this runtime-side
contract or conformance notes:

```bash
python scripts/check_orro_engine_contract.py
```

The checker verifies that witnessd keeps the required artifact names, gates,
trust rules, Depone authority language, and conformance fixture references in
sync.
