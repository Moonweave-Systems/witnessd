---
name: orro
description: ORRO, the Observed Run & Review Orchestrator, turns a goal into an evidence-backed workflow: scout the repo, plan it, run it through witnessd, seal the evidence, and check what the bytes support through Depone. Use for orro, scout, flowplan, proofrun, proofcheck, verdict-backed team execution, evidence-backed automation, and 증거 실행. Published by Moonweave.
---

# orro - evidence-backed workflow runs

Use this skill when an operator asks for ORRO, a proofrun, verdict-backed team
execution, 증거 실행, repo scouting, or evidence-backed automation.

This is distinct from the in-session `team` skill (ephemeral subagent team, no
evidence): ORRO wraps external CLI adapters in signed evidence and re-derives the
verdict through Depone. Reach for ORRO when you need evidence or a verdict; use
`team` for lightweight in-session orchestration.

Source of truth: `SPEC3.md` is the current witnessd x Depone final-form spec.
This skill text is derived from that spec. Moonweave is the publisher/account;
ORRO is the product/tool name. `Superflow` is historical/compatibility naming and
should not be used for new public surfaces.
The cross-engine artifact boundary is summarized in
`docs/orro-engine-contract-v0.md`.

## Public modes

| Mode | Meaning |
| --- | --- |
| `orro` | goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro setup` | one-command setup: provision pinned Depone, initialize home, and write engine lock |
| `orro init` | setup readiness/provision metadata; not proof or assurance |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo exploration and context-pack creation |
| `orro sketch` | advisory ideation: frame, diverge, converge, and hand one direction to flowplan |
| `orro trace` | advisory root-cause investigation before a fix flowplan |
| `orro advisory-provenance-check` | offline Depone re-derivation of sealed sketch/trace provenance; not correctness |
| `orro flow` | guided init/scout/flowplan/proofrun/proofcheck with structured blockers |
| `orro flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `orro proofrun` | precise evidence-backed execution alias |
| `orro proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation gate over persisted run artifacts |
| `orro report` | human-facing summary of observed artifacts and next safe action |
| `orro review` | advisory read-only reviewer-lane execution; emits review receipts only |
| `orro check` | companion: deterministic verify (Depone verdict) + read-only review (advisory); spawns zero execution-adapter lanes; does not claim observed execution |
| `orro demo` | AI-free deterministic shell guardrail demo; Depone re-derives write-scope PASS/FAIL |
| `orro auto --dry-run` | non-executing automation planner; recommendation context only |
| `orro auto --once` | one-step proofcheck/handoff executor; orchestration metadata only |
| `orro auto --until-complete` | bounded post-run proofcheck/handoff loop; orchestration metadata only |
| `orro team init` | scaffold `.orro/team.json` rolepack readiness config; not proof or assurance |
| `orro team go` | one-command flowplan/proofrun/proofcheck/report wrapper; reports Depone verdict |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro auto` | future broader continuation loop behind evidence gates |

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

`python3 -m orro setup --home .witnessd` is the public setup path. It provisions
the pinned Depone verifier when needed, delegates to existing witnessd
initialization/provisioning, and writes `.witnessd/provision.json` plus
`.witnessd/orro-engine-lock.json`. It may use network during setup only. Runtime
and verification remain offline. It does not run ORRO Flow work, verify
evidence, approve merge, or raise assurance. Use
`python3 -m orro setup --home .witnessd --depone-root ../Depone` for
development and tests when you want an explicit local Depone checkout.

`python3 -m orro team init --template developer --yes`
creates `.orro/team.json` rolepack readiness configuration. It validates the
rolepack and keeps tool grants deny-by-default. The default `developer`
template pins a real Codex runner adapter/model rather than a shell reference
lane, but team initialization itself is still not execution, verification,
proof, approval, or assurance.

`python3 -m orro flow "<goal>" --write-scope "<glob>" --adapter codex --json`
threads the existing init, scout, flowplan, proofrun, and proofcheck phases. It
uses model-policy default and emits an `orro-flow-result` with per-phase
artifacts. The command never invents or broadens write scope, requires supplied
rolepack execute scopes to match it, keeps runner and observer directories
separate, and stops at the first existing gate with an actionable structured
blocker. `--runner-sandbox` is a filesystem directory where the runner executes,
not a Codex `sandbox_mode` value or the observer output directory. It does not
approve risky work, opt into reference adapters, weaken Depone verification, or
create a new assurance source.

`python3 -m orro team go "<task>" --repo <repo> --home .witnessd --json`
is the one-command wrapper for `flowplan -> proofrun -> proofcheck -> report`.
If `--profile` is omitted, it calls `orro advise` and uses the recommended
profile. If `--team` is omitted, it selects the deterministic default rolepack
for that profile. Explicit `--profile` and `--team` override automatic routing.
It writes `moonweave-routing-decision.json` with the advisory rule matches,
selected profile, and selected rolepack. That routing artifact cannot change the
evidence verdict and is not proof, approval, or assurance. If a lane does not
touch files, or Depone does not pass the evidence, report blocked/non-zero
rather than upgrading a transcript to success. Shell reference lanes are blocked
unless `--allow-reference-adapter` is passed; allowed reference runs are marked
as not real AI work in the result and report.
`--role-lane-tier auto` is the default: shell lanes run at `quick`/120s and
AI-adapter lanes run at `agentic`/1800s. Override it explicitly with
`quick|agentic|frontier`; an explicit `quick` keeps the 120s budget.

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

`python3 -m orro sketch "<goal>" --repo <repo> --home .witnessd --json`
frames the problem, compares distinct candidate approaches, selects one with a
repository-grounded rationale, and gives each unresolved decision branch one
recommended answer. Its `orro-sketch` output includes an `orro-flowplan-input`
handoff whose goal can be passed to `orro flowplan`.

`python3 -m orro trace "<goal-or-symptom>" --repo <repo> --home .witnessd --json`
orders investigation as observe -> reproduce/localize -> hypothesize -> confirm
root cause. Missing reproduction or confirmation evidence keeps root cause
unconfirmed and blocks a fix proposal; a later confirmed result can seed a fix
flowplan and proofrun.

Both are advisory only. They do not mutate the repo, execute commands or
workers, call Depone, launch proofrun, verify evidence, change an evidence
verdict, approve work, or raise assurance. Their ORRO-native methods live under
`orro/skillpacks/` for frontmatter-selected progressive disclosure. The
external `superpowers` plugin remains untouched as an independent dual path.

`python scripts/check_orro_product_reality.py` validates local dogfood scenarios
for ORRO usefulness: smallest safe workflow, waste avoidance, gate integrity,
artifact fatigue reduction, and clear next action. It is not proof,
verification, telemetry, a benchmark claim, approval, or assurance.

`python scripts/model_routing_benchmark.py` emits model-routing measurement JSON
for the static `(role_kind, tier) -> (adapter, model, budget)` table. Without
`--live`, it loads the deterministic 24-task repository-fixture suite and
calculates routing and budget decisions; it does not call models. With `--live`,
it runs explicit opt-in runner or reviewer tasks and records task success,
Depone signed-bundle verification, elapsed time, turns, available token usage,
estimated cost, and model declaration status. A fallback receipt is recorded
only when the adapter exposes an observed model; otherwise the task carries an
explicit unavailable-model receipt. Live output includes advisory per-role/tier budgets
that cannot exceed the existing model-policy token, cost, or depth ceilings.
Fallback observation is not complete, so multi-candidate fallback remains
disabled. The artifact and advisory are still
measurement only: not proof, not verifier truth, not a benchmark claim, not
approval, and not assurance.

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
are not proof, approval, or assurance. For `code-change`, `--write-scope
'<glob>'` (repeatable) is a bounded write scope input that generates the role
capability directly instead of requiring a prebuilt rolepack. It is never
inferred or defaulted; absent a write scope or explicit rolepack input, role-lane
compilation still fails closed. `review-only` and default
`release-readiness` role-lane plans cannot launch proofrun. `verification-only`
role-lane plans compile declared shell check lanes (`flowplan --check`,
repeatable) with an empty write region; proofrun executes those checks under
observation, a non-zero check exit blocks the lane, and any mutation is
falsified by Depone (`ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`).
Checks run with `PYTHONDONTWRITEBYTECODE=1`; checks must otherwise be
side-effect-free, and tool caches (`.pytest_cache`, `.ruff_cache`, and similar)
should be covered by the target repo's `.gitignore` or redirected outside the
worktree—any file a check writes is honestly falsified by Depone.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change
--role-lanes-out role-lane-plan.json --lane-adapter shell --write-scope
'src/**' --command '<shell>'` compiles a deterministic implementation lane. The
commands are declared intent, the lane region remains exactly the granted write
scope, and the observer captures actual touched files for Depone to re-derive.
`--command` is repeatable, mutually exclusive with `--check`, and invalid for
prompt-driven AI adapters. `orro flow` threads it with model-policy routing off
for the deterministic shell lane. `python3 -m orro demo [--violate]` exercises this
path against a generated sample repo; it is a shell stand-in for an agent, not
evidence of AI execution, approval, or assurance.

AI adapter execution and review subprocesses receive per-lane Python cache
shaping under witnessd state outside the worktree:
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPYCACHEPREFIX`, `RUFF_CACHE_DIR`,
`MYPY_CACHE_DIR`, and an appended pytest `-o cache_dir=` option. Snapshot
observation remains ignore-blind, and review lanes still fail on any real
touched file. This is best-effort coverage for Python development tools;
caches from other toolchains remain observed and enforced.

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
lanes, runs witnessd, and reports the persisted evidence and Depone verdicts
without claiming more than those bytes support.

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
   the default developer team path. Shell lanes are reference/script lanes and
   must be explicitly allowed when used through `team go`.

   ```bash
   python3 -m orro flowplan "<goal>" --root <repo> --profile code-change
   ```

   Treat profile output as intent only. It does not run workers, call live
   models, call Depone verification, approve merge, or raise assurance.

3. Initialize once per repo or session:

   ```bash
   python3 -m orro setup --home .witnessd --json
   python3 -m orro team init --template developer --yes
   python3 -m orro doctor --home .witnessd --json
   ```

4. Run the goal:

   ```bash
   python3 -m orro team go "Create orro/task-output.txt with the exact line: hello ORRO" --repo <repo> --home .witnessd --team .orro/team.json --json
   ```

   Or use the lower-level path when you need to inspect or modify each artifact:

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
- Write-scope verdicts are sealed-declaration/sealed-observation consistency
  checks, not ground-truth surveillance of every filesystem side effect.
- No public claim is stronger than the persisted verdict bytes.
