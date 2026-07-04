---
name: proofrun
description: Run a goal as observer-signed parallel team execution whose completion is re-derived from evidence bytes, not self-declared. Use when the user asks for a proofrun, a verified/proven run, provable team execution, 증명 실행, or to run something "with witnessd". Powered by witnessd × Depone.
---

# proofrun — provable session runs (powered by witnessd × Depone)

Use this skill when an operator asks for a proofrun, a verified or proven run,
provable team execution, 증명 실행, or asks to use witnessd for a goal — any
time "done" must be evidence bytes a verifier re-derives, not the session
agent's own claim.

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
