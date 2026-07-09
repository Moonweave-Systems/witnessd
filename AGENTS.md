# witnessd Session Guidance

Use witnessd when a task asks for provable local team execution, parallel lanes,
or evidence that Depone can re-derive.

## Required Flow

1. Design explicit lanes and regions for the requested goal, or consume a
   provided Depone design artifact when one exists.
2. Run the ORRO wrapper from the repository being worked on:

   ```bash
   python3 -m witnessd init --home .witnessd --depone-root ../depone
   python3 -m witnessd orro proofrun "<goal>" --repo <repo> --home .witnessd
   python3 -m witnessd orro proofcheck <run-dir> --home .witnessd
   python3 -m witnessd orro handoff --proofcheck-verdict <run-dir>/team-ledger-verdict.json
   ```

3. Report from `team-ledger-verdict.json`, not from the session transcript.
   Include the run directory, `team-ledger.json`, `team-ledger-verdict.json`,
   verdict `decision`, lane count, and error count when present.

## Evidence Rule

Until Depone re-derives the run bytes and writes `team-ledger-verdict.json`, the
only honest status is evidence pending or blocked. Do not state a stronger
result based on tool output, model narration, or a lane's own claim.

## Boundaries

- Runtime and verify commands must not use network.
- Use shell/fake adapters for quota-free validation unless the operator
  explicitly authorizes a paid/live adapter run.
- Keep Depone as the non-executing verifier and witnessd as the executing
  runtime.
- Keep ORRO as the wrapper/product surface. Do not merge Depone and witnessd,
  move verifier logic into witnessd, move runtime logic into Depone, or create a
  third engine.
