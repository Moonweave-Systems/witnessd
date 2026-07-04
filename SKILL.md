---
name: witnessd
description: Use witnessd to run an in-session goal through observer-signed team execution and Depone re-derivation.
---

# witnessd Session Runner

Use this skill when an operator asks the session agent to use witnessd for a
goal, asks for provable team execution, or wants a local run that can be checked
by Depone.

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
