# witnessd Session Guidance

Use ORRO when a task asks for provable local team execution, parallel lanes, repo
scouting, progressive context selection, or evidence that Depone can re-derive.
Moonweave is the publisher/account; ORRO is the product/tool name. `Superflow` is
historical compatibility naming.

Source of truth: `SPEC3.md` is the current witnessd x Depone final-form spec.
This guidance is derived from that spec.

## Public modes

- `orro`: goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff
- `orro init`: setup readiness/provision metadata; not proof or assurance
- `orro advise`: non-executing workstyle router for the smallest safe workflow
- `orro scout`: read-only repo profile, context pack, and discovery notes
- `flowplan`: plan-only workflow design and rolepack/workflow compiler surface
- `proofrun`: precise evidence-backed execution alias
- `proofcheck`: offline evidence verification alias
- `orro handoff`: maintainer review package bound to an explicit passing `proofcheck-verdict.json`
- `orro next`: non-executing continuation gate over persisted run artifacts
- `orro auto --dry-run`: non-executing automation planner; recommendation context only
- `orro auto --once`: one-step proofcheck/handoff executor; orchestration metadata only
- `orro auto --until-complete`: bounded post-run proofcheck/handoff loop; orchestration metadata only
- `orro skillpack`: knowledge-as-code and progressive-disclosure support
- `orro doctor`: engine/verifier/adapter/key/MCP/policy readiness check
- `orro auto`: future broader continuation loop behind evidence gates
- `orro ultra`: future high-autonomy profile with stricter gates

## Entrypoint and distribution metadata

`python3 -m orro ...` is the current product-name entrypoint. It is hosted in
the witnessd repo and delegates to the existing `witnessd orro ...` surface. It
is not a standalone ORRO repository and not a third engine.
`python3 -m orro --help` is product-facing and lists only public ORRO commands:
`init`, `advise`, `scout`, `flowplan`, `proofrun`, `proofcheck`, `handoff`,
`next`, `auto`, `doctor`, and `engine-lock`.

Use `python3 -m orro init --home .witnessd --depone-root ../Depone` as the
public setup path. It delegates to existing witnessd initialization/provisioning
and creates readiness metadata such as `.witnessd/provision.json`. It does not
run ORRO Flow work, verify evidence, approve merge, or raise assurance. Use a
local `--depone-root` for development and tests.

Use `python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json`
to write distribution metadata for the pinned witnessd and Depone commits. Use
`python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json`
to check the current local environment for drift against that metadata. A
matching lock is readiness alignment only. A mismatch is readiness-blocked, not
verifier-refuted. The lock is not proof, evidence verification, merge approval,
or assurance, and it must not execute workers.
`orro doctor` checks readiness, not evidence truth.

Use `python3 -m orro advise "<goal>" --repo <repo> --home .witnessd --json` to
get a deterministic workstyle decision before planning or execution. It
recommends the smallest safe workflow and helps non-developers avoid wasteful or
risky AI workflows. It is non-executing advice only: not proof, verifier truth,
approval, or assurance, and it does not replace proofrun, proofcheck, handoff,
or human review for risky changes.

Use `python3 -m orro flowplan "<goal>" --root <repo> --profile code-change` to
compile a deterministic `orro-workflow-plan` for supported profiles:
`code-change`, `review-only`, `verification-only`, `docs-change`, and
`release-readiness`. The workflow plan is intent, not evidence. Roles do not
create assurance by existing. `proofrun` is the first execution phase,
`proofcheck` is the verifier phase, `handoff` is review packaging only, and
broader autonomous `orro auto` and `orro ultra` remain future work.

`python3 -m orro proofrun "<goal>" --repo <repo> --home .witnessd --workflow-plan workflow-plan.json`
first applies a phase gate: the plan must allow `proofrun` through a witnessd
engine call that executes and does not verify. If allowed, proofrun records
`workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json` in the run directory as intended-workflow context.
The binding and role dispatch are not proof that execution followed the plan,
not approval, and not assurance. Depone proofcheck still decides what evidence
supports. `review-only` does not authorize proofrun; formal `orro handoff` still
requires a passing bound proofcheck verdict.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change --role-lanes-out role-lane-plan.json`
writes executable role-lane intent. `python3 -m orro proofrun "<goal>" --repo
<repo> --home .witnessd --workflow-plan workflow-plan.json --role-lane-plan
role-lane-plan.json` validates the workflow hash binding and executes allowed
lanes through existing witnessd team machinery. Role-lane plans are not proof,
approval, or assurance. `review-only`, `verification-only`, and default
`release-readiness` role-lane plans cannot launch proofrun.

`python3 -m orro next <run-dir> --home .witnessd --json` reads persisted run
artifacts and recommends the next safe action. It is non-executing: it does not
run proofcheck, launch workers, retry lanes, repair evidence, write handoff,
verify evidence, approve merge, or raise assurance. `needs-proofcheck` means run
proofcheck next; `ready-for-handoff` means a passing bound proofcheck verdict
exists; `complete` means handoff exists after proofcheck pass. Role status is
derived context only, not proof.

`python3 -m orro auto --dry-run <run-dir> --home .witnessd --json` consumes the
continuation decision and emits an `orro-auto-plan` with the exact command it
would run next. It is non-executing: it does not call Depone, run proofcheck,
write handoff, launch workers, mutate worktrees, verify evidence, approve merge,
or raise assurance. The auto-plan is recommendation context, not proof. Calling
`orro auto` without exactly one mode must fail closed.

`python3 -m orro auto --once <run-dir> --home .witnessd --json` re-checks the
continuation decision and executes at most one allowed step: proofcheck,
handoff, or complete no-op. It must not launch proofrun or workers, call live
models or MCP, repair artifacts, retry or resume lanes, approve merge, or raise
assurance. Its `orro-auto-receipt` is orchestration metadata, not proof or
verifier truth.

`python3 -m orro auto --until-complete <run-dir> --home .witnessd --max-steps 2 --json`
is bounded post-run automation. It requires `--max-steps`; v0 accepts only 1 or
2. It re-checks continuation state before every step and may run proofcheck then
handoff. It must never launch proofrun or workers, repair, retry, resume lanes,
approve merge, or raise assurance. Its `orro-auto-session` is orchestration
metadata, not proof or verifier truth.

The standalone ORRO repo remains deferred until packaging, marketplace, and
version-lock distribution needs justify it. The packaged bare `orro` executable
points at `orro.__main__:main` and must remain an alias layer over the
witnessd-hosted ORRO surface.

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
   `flowplan --profile` may define roles, phases, engine calls, gates, and
   forbidden assurance sources, but it must remain plan-only.
5. Set up ORRO, then run witnessd from the repository being worked on:

   ```bash
   python3 -m orro init --home .witnessd --depone-root ../Depone
   python3 -m orro doctor --home .witnessd --json
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
