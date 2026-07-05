# witnessd Session Guidance

Use ORRO when a task asks for provable local team execution, parallel lanes, repo
scouting, progressive context selection, or evidence that Depone can re-derive.
Moonweave is the publisher/account; ORRO is the product/tool name. `Superflow` is
historical compatibility naming.

Source of truth: `SPEC3.md` is the current witnessd x Depone final-form spec.
This guidance is derived from that spec.

## Public modes

- `orro`: goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff
- `orro scout`: read-only repo profile, context pack, and discovery notes
- `flowplan`: plan-only workflow design
- `proofrun`: precise evidence-backed execution alias
- `proofcheck`: offline evidence verification alias
- `orro handoff`: maintainer review package bound to an explicit passing `proofcheck-verdict.json`
- `orro skillpack`: knowledge-as-code and progressive-disclosure support
- `orro doctor`: engine/verifier/adapter/key/MCP/policy readiness check
- `orro auto`: later continuation loop behind evidence gates
- `orro ultra`: future high-autonomy profile with stricter gates

## Entrypoint and distribution metadata

`python3 -m orro ...` is the current product-name entrypoint. It is hosted in
the witnessd repo and delegates to the existing `witnessd orro ...` surface. It
is not a standalone ORRO repository and not a third engine.
`python3 -m orro --help` is product-facing and lists only public ORRO commands:
`scout`, `flowplan`, `proofrun`, `proofcheck`, `handoff`, `doctor`, and
`engine-lock`.

Use `python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json`
to write distribution metadata for the pinned witnessd and Depone commits. The
lock is not proof, evidence verification, merge approval, or assurance, and it
must not execute workers.

The standalone ORRO repo remains deferred until packaging, marketplace, and
version-lock distribution needs justify it. Console-script packaging for a bare
`orro` executable is deferred because this checkout has no packaging metadata
that can safely install and test it yet.

## Required flow

1. Scout non-trivial work before execution. Do not load the whole repo into one
   context. Produce or update `repo-profile.json`, `context-pack.json`, and
   `discovery-notes.md`.
2. Treat scout output as planning-only. Scout may create `verification-recipe.json`
   for intended checks, but it must not create or claim a fake
   `verification-receipt.json`.
3. Record discovery notes after every two meaningful read/search actions.
4. Design explicit lanes, regions, dependencies, budgets, and verification
   recipes, or consume a provided Depone design artifact when one exists.
5. Run witnessd from the repository being worked on:

   ```bash
   python3 -m witnessd init --home .witnessd --depone-root ../depone
   python3 -m witnessd run "<goal>" --repo <repo> --home .witnessd
   python3 -m witnessd verify <run-dir> --home .witnessd
   ```

6. Report from Depone verdict artifacts, not from the session transcript.
   `team-ledger-verdict.json` records the proofrun team-ledger check. For public
   ORRO handoff, first write `proofcheck-verdict.json` with `proofcheck --out`.
   Include the run directory, `team-ledger.json`, the verdict artifact path,
   verdict `decision`, lane count, and error count when present.
7. When changes are prepared for review, create or reference `orro-handoff.json`
   only after `proofcheck-verdict.json` exists and has `decision: "pass"`.

## Evidence rule

Until Depone re-derives the run bytes and writes `team-ledger-verdict.json`, the
only honest status is evidence pending or blocked. Do not state a stronger result
based on tool output, model narration, a lane's own claim, MCP output, skill text,
IDE terminal state, or tmux pane state.

For handoff, `team-ledger-verdict.json` alone is not enough. `handoff` /
`orro handoff` must fail closed until an explicit passing
`proofcheck-verdict.json` exists in the run directory.

A scout-only artifact directory is not execution proof. If proofcheck blocks it
because a verification receipt or other required execution artifact is missing,
report that as the correct result rather than upgrading it.

## Knowledge and tool receipts

Use `SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and ORRO skillpacks as
knowledge-as-code. Load relevant bodies only after frontmatter or path matching
shows they apply.

If a lane uses an MCP server or external tool bridge, the run must include an
MCP/tool receipt. MCP output is an observed external fact, not final verifier
truth.

## Boundaries

- Runtime and verify commands must not use network.
- Use shell/fake adapters for quota-free validation unless the operator
  explicitly authorizes a paid/live adapter run.
- Keep Depone as the non-executing verifier and witnessd as the executing
  runtime.
