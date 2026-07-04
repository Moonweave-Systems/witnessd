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
