---
name: superflow
description: Superflow turns a goal into an evidence-backed workflow: plan it, run it through witnessd, seal the evidence, and check what the bytes support through Depone. Use for superflow, proofrun, provable team execution, 증명 실행, or evidence-backed automation. Published by Moonweave.
---

# superflow — evidence-backed workflow runs

Use this skill when an operator asks for Superflow, a proofrun, provable team
execution, 증명 실행, or evidence-backed automation.

Source of truth: `SPEC3.md` is the current witnessd × Depone final-form spec.
This skill text is derived from that spec. Moonweave is the publisher/account;
Superflow is the product/tool name.

## Public modes

| Mode | Meaning |
| --- | --- |
| `superflow` | goal -> plan -> run -> evidence -> verifier summary |
| `flowplan` | plan-only workflow design |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `superflow auto` | later continuation loop behind evidence gates |
| `superflow ultra` | future high-autonomy profile with stricter gates |

## Repository and install boundary

This is the single user-facing skill surface. Do not ask normal users to install
separate Depone and witnessd skills for one workflow.

The skill may live in the witnessd repo while the product surface is thin,
because Superflow starts execution and witnessd owns execution. Depone stays a
pinned verifier dependency and is invoked only to re-derive persisted evidence
bytes.

A future standalone `Superflow` repo may package marketplace manifests,
host-specific plugin files, examples, product docs, and engine version locks. It
must remain a wrapper/distribution repo, not a place to duplicate witnessd runtime
logic or Depone verifier logic.

## Contract

The session agent does not certify its own work. It designs or receives lanes,
runs witnessd, and reports only evidence that Depone re-derived from bytes.

Required output evidence:

- run directory path
- `team-ledger.json` path
- `team-ledger-verdict.json` path
- verdict `decision`
- lane count and any error count present in the verdict

## Workflow

1. Choose explicit lanes for the goal. If a Depone design artifact is already
   available, use its lane/region shape. If not, use explicit witnessd lanes or
   the default shell-lane quickstart path.
2. Initialize once per repo or session:

   ```bash
   python3 -m witnessd init --home .witnessd --depone-root ../depone
   ```

3. Run the goal:

   ```bash
   python3 -m witnessd run "<goal>" --repo <repo> --home .witnessd
   ```

4. Re-check the emitted bytes:

   ```bash
   python3 -m witnessd verify <run-dir> --home .witnessd
   ```

5. Report the verdict fields from `team-ledger-verdict.json`. If the verdict is
   missing, blocked, unreadable, or not re-derived, report evidence pending or
   blocked with the exact error. Do not upgrade the result from the session
   transcript.

## Boundaries

- Runtime and verify paths are offline. Do not fetch, clone, or install while
  running or verifying.
- Depone verifies; witnessd executes. Do not import Depone into witnessd runtime
  code.
- No public claim is stronger than the persisted verdict bytes.
