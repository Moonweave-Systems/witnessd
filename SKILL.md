---
name: superflow
description: Superflow turns a goal into an evidence-backed workflow: scout the repo, plan it, run it through witnessd, seal the evidence, and check what the bytes support through Depone. Use for superflow, scout, flowplan, proofrun, proofcheck, provable team execution, 증명 실행, or evidence-backed automation. Published by Moonweave.
---

# superflow — evidence-backed workflow runs

Use this skill when an operator asks for Superflow, a proofrun, provable team
execution, 증명 실행, repo scouting, or evidence-backed automation.

Source of truth: `SPEC3.md` is the current witnessd × Depone final-form spec.
This skill text is derived from that spec. Moonweave is the publisher/account;
Superflow is the product/tool name.

## Public modes

| Mode | Meaning |
| --- | --- |
| `superflow` | goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `superflow scout` | read-only repo exploration and context-pack creation |
| `flowplan` | plan-only workflow design |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `superflow handoff` | maintainer review package bound to evidence |
| `superflow skillpack` | knowledge-as-code and progressive-disclosure support |
| `superflow doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
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

The session agent does not certify its own work. It scouts, designs or receives
lanes, runs witnessd, and reports only evidence that Depone re-derived from bytes.

Required output evidence:

- run directory path
- `repo-profile.json` path when a scout step ran
- `context-pack.json` path when a scout step ran
- `verification-recipe.json` path when checks are declared
- `team-ledger.json` path
- `team-ledger-verdict.json` path
- verdict `decision`
- lane count and any error count present in the verdict

## Progressive disclosure rules

Do not load the whole repository into context. For non-trivial work:

1. run a read-only scout step,
2. create or update `repo-profile.json`,
3. build `context-pack.json` for relevant paths only,
4. write `discovery-notes.md` after every two meaningful read/search actions,
5. create a `verification-recipe.json` before implementation when checks exist,
6. run witnessd only after the plan and checks are clear.

Use existing `SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and Superflow
skillpacks as knowledge-as-code. Load only the relevant skill body after
frontmatter matching.

If MCP or external tools are used, require receipts. Do not treat external tool
output as verifier truth.

## Workflow

1. Scout before non-trivial implementation:

   ```bash
   python3 -m witnessd scout "<goal>" --repo <repo> --home .witnessd
   ```

   If `scout` is not implemented yet, perform read-only repo inspection and write
   the same artifacts manually in the run directory.

2. Choose explicit lanes for the goal. If a Depone design artifact is already
   available, use its lane/region shape. If not, use explicit witnessd lanes or
   the default shell-lane quickstart path.

3. Initialize once per repo or session:

   ```bash
   python3 -m witnessd init --home .witnessd --depone-root ../depone
   ```

4. Run the goal:

   ```bash
   python3 -m witnessd run "<goal>" --repo <repo> --home .witnessd
   ```

5. Re-check the emitted bytes:

   ```bash
   python3 -m witnessd verify <run-dir> --home .witnessd
   ```

6. Prepare a handoff package when code/docs changed:

   ```text
   pr-handoff.json
     run_id
     evidence_dir
     changed_files
     verification_recipe_results
     unresolved_risks
     human_required_actions
   ```

7. Report the verdict fields from `team-ledger-verdict.json`. If the verdict is
   missing, blocked, unreadable, or not re-derived, report evidence pending or
   blocked with the exact error. Do not upgrade the result from the session
   transcript.

## Boundaries

- Runtime and verify paths are offline. Do not fetch, clone, or install while
  running or verifying.
- Depone verifies; witnessd executes. Do not import Depone into witnessd runtime
  code.
- Skill text, MCP output, IDE terminals, tmux panes, and session transcripts are
  not verdicts.
- No public claim is stronger than the persisted verdict bytes.
