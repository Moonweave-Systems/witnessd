# witnessd Session Guidance

Use Moonweave Superflow when a task asks for provable local team execution,
parallel lanes, or evidence that Depone can re-derive.

Source of truth: `SPEC3.md` is the current witnessd × Depone final-form spec.
This guidance is derived from that spec.

## Public modes

- `superflow`: goal -> plan -> run -> evidence -> verifier summary
- `flowplan`: plan-only workflow design
- `proofrun`: precise evidence-backed execution alias
- `proofcheck`: offline evidence verification alias
- `superflow auto`: later continuation loop behind evidence gates
- `superflow ultra`: future high-autonomy profile with stricter gates

## Required flow

1. Design explicit lanes and regions for the requested goal, or consume a
   provided Depone design artifact when one exists.
2. Run witnessd from the repository being worked on:

   ```bash
   python3 -m witnessd init --home .witnessd --depone-root ../depone
   python3 -m witnessd run "<goal>" --repo <repo> --home .witnessd
   python3 -m witnessd verify <run-dir> --home .witnessd
   ```

3. Report from `team-ledger-verdict.json`, not from the session transcript.
   Include the run directory, `team-ledger.json`, `team-ledger-verdict.json`,
   verdict `decision`, lane count, and error count when present.

## Evidence rule

Until Depone re-derives the run bytes and writes `team-ledger-verdict.json`, the
only honest status is evidence pending or blocked. Do not state a stronger result
based on tool output, model narration, or a lane's own claim.

## Boundaries

- Runtime and verify commands must not use network.
- Use shell/fake adapters for quota-free validation unless the operator
  explicitly authorizes a paid/live adapter run.
- Keep Depone as the non-executing verifier and witnessd as the executing
  runtime.
