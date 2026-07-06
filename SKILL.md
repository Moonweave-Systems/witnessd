---
name: orro
description: ORRO, the Observed Run & Review Orchestrator, turns a goal into an evidence-backed workflow: scout the repo, plan it, run it through witnessd, seal the evidence, and check what the bytes support through Depone. Use for orro, scout, flowplan, proofrun, proofcheck, provable team execution, evidence-backed automation, and 증명 실행. Published by Moonweave.
---

# orro - evidence-backed workflow runs

Use this skill when an operator asks for ORRO, a proofrun, provable team
execution, 증명 실행, repo scouting, or evidence-backed automation.

Source of truth: `SPEC3.md` is the current witnessd x Depone final-form spec.
This skill text is derived from that spec. Moonweave is the publisher/account;
ORRO is the product/tool name. `Superflow` is historical/compatibility naming and
should not be used for new public surfaces.

## Public modes

| Mode | Meaning |
| --- | --- |
| `orro` | goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro init` | setup readiness/provision metadata; not proof or assurance |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo exploration and context-pack creation |
| `flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation gate over persisted run artifacts |
| `orro report` | human-facing summary of observed artifacts and next safe action |
| `orro auto --dry-run` | non-executing automation planner; recommendation context only |
| `orro auto --once` | one-step proofcheck/handoff executor; orchestration metadata only |
| `orro auto --until-complete` | bounded post-run proofcheck/handoff loop; orchestration metadata only |
| `orro skillpack` | knowledge-as-code and progressive-disclosure support |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro auto` | future broader continuation loop behind evidence gates |
| `orro ultra` | future high-autonomy profile with stricter gates |

## Repository and install boundary

This is the single user-facing skill surface. Do not ask normal users to install
separate Depone and witnessd skills for one workflow.

The skill may live in the witnessd repo while the product surface is thin,
because ORRO starts execution and witnessd owns execution. Depone stays a pinned
verifier dependency and is invoked only to re-derive persisted evidence bytes.
The current product-name CLI is `python3 -m orro ...`, a thin module entrypoint
hosted in witnessd that delegates to the existing `witnessd orro ...` command
surface. It is not a standalone ORRO repo and not a third engine.
Its help surface is ORRO-facing and lists only public ORRO commands; witnessd
engine-internal commands stay behind the witnessd CLI. The packaged `orro`
console script points at the same module entrypoint and must remain an alias
layer, not a separate parser or engine.

`python3 -m orro init --home .witnessd --depone-root ../Depone` is the public
setup path. It delegates to existing witnessd initialization/provisioning and
creates readiness metadata such as `.witnessd/provision.json`. It does not run
ORRO Flow work, verify evidence, approve merge, or raise assurance. Use a local
`--depone-root` for development and tests.

`python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json`
writes distribution metadata for the pinned witnessd and Depone commits.
`python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json`
checks the current local environment for drift against that metadata. A matching
lock is readiness alignment only. A mismatch is readiness-blocked, not
verifier-refuted. The lock is not proof, evidence verification, merge approval,
or assurance.

`orro doctor` checks readiness, not evidence truth.

`python3 -m orro advise "<goal>" --repo <repo> --home .witnessd --json` is the
developer-judgment/workstyle layer. It returns an `orro-workstyle-decision` with
the recommended task class, profile, path, skip list, gates, and reasons. It is
non-executing advice only and is not proof, verifier truth, approval, or
assurance. It helps non-developers avoid wasteful or risky AI workflows, but it
does not replace proofrun, proofcheck, handoff, or human review for risky
changes.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change --out workflow-plan.json`
emits a deterministic `orro-workflow-plan` intent artifact. Supported profiles
are `code-change`, `review-only`, `verification-only`, `docs-change`, and
`release-readiness`. The plan maps roles, phases, engine calls, gates, and
forbidden assurance sources. It is not evidence. Roles do not create assurance by
existing. `proofrun` is the first execution phase, `proofcheck` is the verifier
phase, `handoff` is review packaging only, and broader autonomous `orro auto`
and `orro ultra` remain future work.

`python3 -m orro proofrun "<goal>" --repo <repo> --home .witnessd --workflow-plan workflow-plan.json`
first checks that the workflow plan allows `proofrun` through a witnessd engine
call that executes and does not verify. If the phase is forbidden, it fails
before creating a run directory. If allowed, it records `workflow-plan.json`,
`workflow-plan-binding.json`, and `workflow-role-dispatch.json` in the run
directory. The binding and role dispatch are review context only. They are not
proof that execution followed the plan, not approval, and not assurance. Depone
proofcheck still decides what the evidence supports. A `review-only` profile
does not authorize proofrun or make `orro handoff` succeed without a passing
bound `proofcheck-verdict.json`.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change --role-lanes-out role-lane-plan.json`
writes executable role-lane intent. `python3 -m orro proofrun "<goal>" --repo
<repo> --home .witnessd --workflow-plan workflow-plan.json --role-lane-plan
role-lane-plan.json` validates the role-lane plan against the workflow hash and
executes allowed lanes through existing witnessd team machinery. Role-lane plans
are not proof, approval, or assurance. `review-only`, `verification-only`, and
default `release-readiness` role-lane plans cannot launch proofrun.

`python3 -m orro next <run-dir> --home .witnessd --json` reads persisted
artifacts and recommends the next safe action. It does not run proofcheck,
launch workers, retry lanes, repair evidence, write handoff, verify evidence,
approve merge, or raise assurance. `needs-proofcheck` means run proofcheck next;
`ready-for-handoff` means a passing bound proofcheck verdict exists; `complete`
means handoff exists after proofcheck pass. Role status is derived from observed
artifacts only and is not proof. Malformed workflow bindings, role-lane
bindings, role dispatch, team ledgers, and team-ledger verdicts block
continuation instead of counting as execution evidence.

`python3 -m orro report <run-dir> --home .witnessd --json` is the human-facing
compression layer. It summarizes observed artifacts, proofcheck and handoff
state, next safe action, reviewer focus, and do-not-trust boundaries. It does
not execute, run proofcheck, write handoff, verify evidence, approve merge,
raise assurance, replace proofcheck, or replace human review.

`python3 -m orro auto --dry-run <run-dir> --home .witnessd --json` consumes
`orro next` state and emits an `orro-auto-plan` with the exact command it would
run next. It does not run proofcheck, call Depone, launch workers, write
handoff, mutate worktrees, approve merge, verify evidence, or raise assurance.
The auto-plan is recommendation context only, not proof. `orro auto` without
exactly one mode must fail closed.

`python3 -m orro auto --once <run-dir> --home .witnessd --json` re-checks
continuation state and executes at most one allowed step. In v0 that means
proofcheck, handoff, or complete no-op only. It never launches proofrun or
workers, calls live models or MCP, repairs artifacts, retries or resumes lanes,
approves merge, or raises assurance. When it runs proofcheck, verification is
delegated to Depone. The auto receipt is orchestration metadata, not proof or
verifier truth.

`python3 -m orro auto --until-complete <run-dir> --home .witnessd --max-steps 2 --json`
is a bounded post-run loop over proofcheck and handoff only. It requires
`--max-steps`, re-checks continuation state before every step, and stops on
blocked, evidence-pending, invalid-run-dir, max-steps, or complete. It never
launches proofrun or workers. The auto session is orchestration metadata, not
proof or verifier truth.

All auto modes inherit the continuation fail-closed rules: malformed, stale,
copied, or unbound critical artifacts must block rather than trigger proofcheck,
handoff, or complete status.

A future standalone `ORRO` repo may package marketplace manifests, host-specific
plugin files, examples, product docs, and engine version locks. It must remain a
wrapper/distribution repo, not a place to duplicate witnessd runtime logic or
Depone verifier logic.

Compatibility aliases such as `superflow` may remain during migration, but ORRO
is the canonical product and skill surface.

## Contract

The session agent does not certify its own work. It scouts, designs or receives
lanes, runs witnessd, and reports only evidence that Depone re-derived from bytes.

Required output evidence:

- run directory path
- `repo-profile.json` path when a scout step ran
- `context-pack.json` path when a scout step ran
- `verification-recipe.json` path when checks are declared
- `verification-receipt.json` path only after a command actually ran
- `team-ledger.json` path
- `team-ledger-verdict.json` path
- `proofcheck-verdict.json` path before handoff
- verdict `decision`
- lane count and any error count present in the verdict

Scout artifacts are planning-only. `orro scout` must not create a fake
`verification-receipt.json`, and a scout-only directory must not be reported as a
`proofcheck` pass.

## Progressive disclosure rules

Do not load the whole repository into context. For non-trivial work:

1. run a read-only scout step,
2. create or update `repo-profile.json`,
3. build `context-pack.json` for relevant paths only,
4. write `discovery-notes.md` after every two meaningful read/search actions,
5. create a `verification-recipe.json` before implementation when checks exist,
6. run witnessd only after the plan and checks are clear.

Use existing `SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and ORRO
skillpacks as knowledge-as-code. Load only the relevant skill body after
frontmatter matching.

If MCP or external tools are used, require receipts. Do not treat external tool
output as verifier truth.

## Workflow

1. Scout before non-trivial implementation:

   ```bash
   python3 -m orro scout "<goal>" --repo <repo> --home .witnessd
   ```

   If `scout` is not implemented yet, perform read-only repo inspection and write
   the same artifacts manually in the run directory. Scout may create planning
   artifacts and verification recipes, but it does not prove execution.

2. Choose explicit lanes for the goal. If a Depone design artifact is already
   available, use its lane/region shape. If not, use explicit witnessd lanes or
   the default shell-lane quickstart path.

   ```bash
   python3 -m orro flowplan "<goal>" --root <repo> --profile code-change
   ```

   Treat profile output as intent only. It does not run workers, call live
   models, call Depone verification, approve merge, or raise assurance.

3. Initialize once per repo or session:

   ```bash
   python3 -m orro init --home .witnessd --depone-root ../Depone
   python3 -m orro doctor --home .witnessd --json
   ```

4. Run the goal:

   ```bash
   python3 -m witnessd run "<goal>" --repo <repo> --home .witnessd --workflow-plan workflow-plan.json
   ```

5. Re-check the emitted bytes and write the public proofcheck verdict when a
   handoff is needed:

   ```bash
   python3 -m orro proofcheck <run-dir> \
     --home .witnessd \
     --out <run-dir>/proofcheck-verdict.json
   ```

6. Prepare a handoff package when code/docs changed:

   ```bash
   python3 -m orro handoff <run-dir> \
     --out <run-dir>/orro-handoff.json
   ```

   `team-ledger-verdict.json` from proofrun is not enough by itself. Handoff
   requires `proofcheck-verdict.json` to exist, parse as JSON, and have
   `decision: "pass"`.

7. Report the verdict fields from the Depone verdict artifact. If the verdict is
   missing, blocked, unreadable, not pass, or not re-derived, report evidence
   pending or blocked with the exact error. Do not upgrade the result from the
   session transcript.

## Boundaries

- Runtime and verify paths are offline. Do not fetch, clone, or install while
  running or verifying.
- Depone verifies; witnessd executes. Do not import Depone into witnessd runtime
  code.
- Skill text, MCP output, IDE terminals, tmux panes, and session transcripts are
  not verdicts.
- Scout-only planning artifacts are not execution proof.
- No public claim is stronger than the persisted verdict bytes.
