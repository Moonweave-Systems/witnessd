# witnessd

`witnessd` is the executing runtime engine for **ORRO** (Observed Run & Review
Orchestrator), published under the Moonweave account. It runs local lanes,
records what happened, signs the evidence, and leaves bytes that Depone can
re-derive offline.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

`Superflow` was the earlier product-surface name. New public docs should use
ORRO. Existing `superflow` commands, fixture paths, or artifact kinds may remain
as compatibility aliases during migration.

## 10-minute quickstart

```bash
cd witnessd
python3 -m orro setup --home .witnessd --json
python3 -m orro doctor --home .witnessd --json
python3 -m orro advise "verify the repository diff" --repo . --home .witnessd --json
scout_json="$(python3 -m orro scout "verify the repository diff" --repo . --home .witnessd)"
run_dir="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$scout_json")"
python3 -m orro flowplan "verify the repository diff" --root . --profile verification-only --check "git diff --check" --out "$run_dir/workflow-plan.json" --role-lanes-out "$run_dir/role-lane-plan.json"
run_json="$(python3 -m orro proofrun "verify the repository diff" --repo . --home .witnessd --workflow-plan "$run_dir/workflow-plan.json" --role-lane-plan "$run_dir/role-lane-plan.json" --run-dir "$run_dir" --json)"
run_dir="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$run_json")"
python3 -m orro proofcheck "$run_dir" --home .witnessd --out "$run_dir/proofcheck-verdict.json" --json
python3 -m orro next "$run_dir" --home .witnessd --json
python3 -m orro auto --dry-run "$run_dir" --home .witnessd --json
python3 -m orro auto --until-complete "$run_dir" --home .witnessd --max-steps 2 --json
python3 -m orro report "$run_dir" --home .witnessd --json
python3 -m orro next "$run_dir" --home .witnessd --json
```

This front-door path is a real, deterministic, auth-free verification run. The
declared `git diff --check` command executes in a verification-only lane with no
granted write scope, and Depone independently checks the resulting evidence.

### Offline demo (not real AI work)

For an intentional script/fixture demonstration without model authentication,
the reference adapter remains available:

```bash
python3 -m orro proofrun "write two independent files" \
  --repo . \
  --home .witnessd \
  --allow-reference-adapter \
  --json
```

This path is explicitly labeled `not_real_ai_work: true`; it demonstrates the
offline capture machinery and is not evidence of AI execution.

For a code-change goal, `orro flow` removes the artifact-threading steps while
keeping the same phase gates:

```bash
python3 -m orro flow "Create pkg/output.txt" \
  --write-scope "pkg/output.txt" \
  --adapter codex \
  --home .witnessd \
  --json
```

It runs `init -> scout -> flowplan -> proofrun -> proofcheck`, uses
`--model-policy default`, and returns one `orro-flow-result` containing the run
directory, final verdict, and per-phase artifacts. `--write-scope` is required,
repeatable, and never inferred or widened. A supplied rolepack must use exactly
the same execute scopes. The runner sandbox is created outside the observer run
directory unless `--runner-sandbox` is supplied; overlapping paths fail closed.
Every first-phase failure is returned as an actionable structured blocker. No
risky-change, reference-adapter, write-scope, or Depone gate is bypassed.

`orro team init` only writes readiness configuration (`.orro/team.json`). It is
not execution, verification, proof, or assurance. `orro team go` is the ergonomic
wrapper for the longer `flowplan -> proofrun -> proofcheck -> report` path: it
threads the intermediate paths, passes the user's task text into the runner lane
prompt, writes `proofcheck-verdict.json`, and exits non-zero when the lane did no
work or Depone does not pass the evidence.

```bash
python3 -m orro team init --template developer --yes
python3 -m orro team go "Create orro/task-output.txt with the exact line: hello ORRO" --repo . --home .witnessd --json
```

The default `developer` template uses a real Codex runner model. Shell reference
lanes are allowed only for intentional script/test runs: `team go` blocks them
unless `--allow-reference-adapter` is passed, and even then the result and report
mark the run as reference/script work, not real AI work.

When `--profile` is omitted, `team go` calls `orro advise` and uses the
recommended profile. When `--team` is omitted, it selects the deterministic
default rolepack for that profile. Explicit `--profile` and `--team` always win.
The run writes `moonweave-routing-decision.json` so the advisory routing choice,
rule matches, selected profile, and selected rolepack are visible. That artifact
cannot change the evidence verdict and is not proof, approval, or assurance.
`--role-lane-tier auto` is the default: shell lanes run at `quick`/120s and
AI-adapter lanes run at `agentic`/1800s. Override it explicitly with
`quick|agentic|frontier`; an explicit `quick` keeps the 120s budget.

The `proofrun` command prints JSON. Use its `run_dir` field for the proofcheck
and handoff steps. A direct shell invocation (`orro proofrun --adapter shell --
<command>`) is capture-only and is not proofcheckable by itself. It does not
contain the workflow packet that proofcheck requires. Start with `orro scout`,
put the flowplan and proofrun artifacts in the scout run directory, or use
`orro team go`. The front-door workflow above is copy-pasteable and uses a
verification-only check lane; the separately labeled offline demo uses the
intentional shell reference adapter and is not real AI work.
`proofcheck` must write an explicit `proofcheck-verdict.json` before packaging
the handoff:

```bash
python3 -m orro proofcheck .witnessd/runs/<run-dir> \
  --home .witnessd \
  --out .witnessd/runs/<run-dir>/proofcheck-verdict.json
python3 -m orro handoff .witnessd/runs/<run-dir> \
  --out .witnessd/runs/<run-dir>/orro-handoff.json
```

`team-ledger-verdict.json` emitted during a proofrun is not enough by itself for
handoff. `handoff` / `orro handoff` fails closed unless
`proofcheck-verdict.json` exists, is readable JSON, has `decision: "pass"`, and
contains the ORRO binding for the current evidence snapshot.

## Honest limits

witnessd signs the default single-machine flow with an operator key generated
by that same runtime. Verification labels this `trust_anchor: "self-signed"`:
the signature and evidence bytes remain checkable, but there is no independent
out-of-band anchor and the result must not claim observer-signed provenance or
A1/A2 from that key. To make an external trust anchor eligible, provision the
matching operator keypair outside the runtime and supply its public key through
`DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`; outputs then report
`trust_anchor: "operator-provided"`. Observer-signed/A2 language additionally
requires real observer/runner separation.

Capture defaults to the `redacted` profile. It replaces known local values such
as the prompt, selected paths, worktree, and `CODEX_HOME`, and every profile
also best-effort-scrubs a fixed set of high-confidence secret patterns from
captured output before hashing and signing. When matched, the scrub and its
rule-level digests are recorded in `redaction-manifest.json`; explicit `full`
capture remains available for raw local paths and prompts.

Secret-pattern scrubbing is high-confidence-patterns-only and is not a
completeness guarantee. Operators must still avoid putting secrets where a
lane, command, model, tool, or adapter can print them.

witnessd may emit self-declared runtime facts and `DELAYED_NOTARY` style
post-hoc records, but those records do not upgrade trust. A2 requires a
dedicated observer uid, a separate runner, and observer-owned evidence paths that
are not writable by the runner. Depone decides what the persisted bytes support.
For role-capability write scope, Depone can re-derive whether sealed touched-file
observations fit the sealed declared scope. That is tamper-evident consistency of
persisted evidence, not ground-truth proof of every host filesystem side effect.
Fulcio/Rekor keyless and transparency anchoring remain roadmap; the reserved
profile currently fails closed with `ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED`.

## Source of truth

[`SPEC3.md`](SPEC3.md) is the only top-level witnessd product/runtime authority.
`SPEC.md`, `SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`,
`AGENTS.md`, fixture notes, and release notes are derived, wave-specific, or
historical. If they conflict with `SPEC3.md`, `SPEC3.md` wins.

For the Depone verifier contract itself, Depone's `docs/spec.md` is the
authority. For the repo documentation map, see [`docs/README.md`](docs/README.md).
The cross-engine ORRO boundary is summarized in
[`docs/orro-engine-contract-v0.md`](docs/orro-engine-contract-v0.md).
The standalone ORRO product/distribution repository now lives at
<https://github.com/Moonweave-Systems/ORRO>. It owns product onboarding,
examples, distribution drafts, and e2e smoke-contract docs; it does not contain
witnessd runtime code or Depone verifier logic.

## User-facing names

| Public surface | Purpose |
| --- | --- |
| ORRO | flagship product/tool: evidence-governed agent workflow orchestrator |
| ORRO Flow | `scout -> flowplan -> proofrun -> proofcheck -> handoff` |
| `orro` | flagship goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro setup` | one-command setup: provision pinned Depone, initialize home, and write engine lock |
| `orro init` | setup readiness/provision metadata; not proof or assurance |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo exploration, repo profile, context pack, and discovery notes |
| `orro sketch` | validates and seals an agent-authored advisory direction |
| `orro trace` | validates, gates, and seals an agent-authored root-cause record |
| `orro advisory-provenance-check` | offline Depone v110 re-derivation of sealed sketch/trace provenance; not correctness |
| `orro flow` | guided init/scout/flowplan/proofrun/proofcheck with first-phase structured blockers |
| `orro flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `orro proofrun` | precise evidence-backed execution alias |
| `orro proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation gate over persisted run artifacts |
| `orro report` | human-facing summary of observed ORRO artifacts and next safe action |
| `orro review` | advisory read-only reviewer-lane execution; emits review receipts only |
| `orro check` | companion: deterministic verify (Depone verdict) + read-only review (advisory); spawns zero execution-adapter lanes; does not claim observed execution |
| `orro auto --dry-run` | non-executing automation planner that recommends the next command |
| `orro auto --once` | one-step executor for proofcheck or handoff only |
| `orro auto --until-complete` | bounded post-run loop over proofcheck and handoff only |
| `orro team init` | scaffold `.orro/team.json` rolepack readiness config; not proof or assurance |
| `orro team go` | one-command flowplan/proofrun/proofcheck/report wrapper; reports Depone verdict |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro auto` | future broader resume/continuation loop behind evidence gates |

`witnessd` is the engine name, not the main session skill name. `Moonweave` is the
publisher/account namespace, not the tool name.

## Repository strategy

Development currently stays in two engine repositories:

```text
Depone   = verifier engine and evidence contract
witnessd = execution engine, evidence emitter, and near-term ORRO surface
ORRO     = product/distribution/wrapper repository
```

The user-facing install should still be one thing: ORRO. Do not ask normal users
to install separate Depone and witnessd skills. In the near term, this repo hosts
the thin ORRO entrypoint because ORRO starts execution and witnessd owns
execution. `python3 -m orro ...` delegates to the existing `witnessd orro ...`
surface. The standalone `Moonweave-Systems/ORRO` repo now exists as the
product/distribution/wrapper repo, but engine code remains in Depone and
witnessd. It must not duplicate witnessd runtime behavior, Depone proofcheck
logic, or become a third engine. Depone remains a pinned verifier dependency.

Public ORRO setup starts with `orro setup`, which provisions a pinned Depone
verifier when needed, delegates to existing witnessd initialization/provisioning,
and creates readiness metadata such as `.witnessd/provision.json` plus
`.witnessd/orro-engine-lock.json`. It does not run ORRO Flow work, verify
evidence, approve merge, or raise assurance. For local development, an explicit
Depone checkout remains available:

```bash
python3 -m orro setup --home .witnessd --depone-root ../Depone --json
python3 -m orro doctor --home .witnessd --json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
```

`orro doctor` checks readiness, not evidence truth. When
`<home>/orro-engine-lock.json` exists, doctor compares the provisioned witnessd
and Depone commits against it by default. A doctor pass means that readiness
metadata is consistent with the recorded engine pair; it does not mean the pair
passed the ORRO end-to-end compatibility smoke. An engine lock records the
pinned engine commits for distribution tooling.
`orro-engine-lock.json` is distribution metadata only. `--out` writes the local
witnessd/Depone pin metadata; `--check` compares the current local environment
against that metadata to detect distribution drift. A matching lock means
readiness alignment only. It is not proof, evidence verification, merge approval,
or an assurance increase. A mismatch is readiness-blocked, not verifier-refuted.

`orro advise "<goal>" --repo . --home .witnessd --json` is the non-executing
workstyle router. It classifies the goal into a deterministic task class such as
`trivial-change`, `docs-change`, `code-change`, `review-only`,
`verification-only`, `release-readiness`, or `risky-change`, then recommends the
smallest safe ORRO path. It helps non-developers avoid wasteful or risky AI
workflows, but it does not replace proofrun, proofcheck, handoff, or human
review for risky changes. Its `orro-workstyle-decision` is advice only: not
proof, verifier truth, merge approval, or assurance.
When sketch or trace output is sealed, that record is auditable provenance only;
it does not establish that the recommended workflow or diagnosis is correct.

`orro sketch "<goal>" --decision sketch.json --repo . --home .witnessd --json`
validates and seals the calling agent's own frame, candidates, choice, rejection
reasons, no-gos, and rabbit holes. `--decision` is a path to a JSON file, not
inline text; a minimal accepted sketch decision is documented at
`tests/fixtures/orro-sketch-decision.json`.
`orro trace "<symptom>" --decision trace.json --repo . --home .witnessd --json`
validates the agent's hypotheses and claimed
tier against the symptom-bound `orro-trace-reproduction.json` from a prior actual
run. The decision may bind either the source-file SHA-256 or the canonical sealed
receipt SHA-256. A claimed `confirmed` tier is refused unless the receipt binds a
real failing command, exit code, transcript digest, symptom, and authored
discriminating probe, and records a ruled-out rival plus red-to-green confirmation.
Suspected, speculative, and unconfirmed records impose no minimum hypothesis count
beyond shape consistency. Without `--decision`, each command
emits only a non-authoritative scaffold stamped `agent_authored=false` and
`degraded=true`; the harness-authored fallback is intended for headless/CI use.
Both surfaces are read-only advisory context, not evidence, verifier truth,
approval, or assurance; neither can mutate the inspected repo, change an evidence
verdict, or launch proofrun. Their skillpacks are reference knowledge for the
agent, not CLI-enforced ceremonies or CLI-generated reasoning.

With `--out`, sketch and trace also write `advisory-provenance-bundle.json` and
the Depone `v110.advisory_provenance` `evidence-contract.json`; trace additionally
copies the prior run receipt into the sealed subject set. A confirmed trace also
seals `orro-trace-execution.json` as a digest-bound subject and records its canonical
hash in the decision. That subject must already exist as a complete, self-hashed
`execution` object inside the prior-run reproduction; witnessd validates and
canonicalizes it but does not invent missing execution metadata. Re-derive the
separate provenance track with:

```bash
python3 -m orro advisory-provenance-check <artifact-dir> --home .witnessd --json
```

`PASS` means the record is tamper-evident and the chosen direction or confirmed
tier is backed by the sealed bytes. It is not proof, execution-evidence truth,
approval, assurance, or a claim that the direction/root cause is correct.

`python scripts/check_orro_product_reality.py` validates local dogfood scenarios
for ORRO usefulness: smallest safe workflow, waste avoidance, proofcheck/handoff
gate integrity, artifact fatigue reduction, and clear next action. It is not
proof, verification, telemetry, a benchmark claim, approval, or assurance.

`python scripts/model_routing_benchmark.py` emits measurement JSON for the static
model-routing table. By default it is offline and deterministic: it loads a
24-task suite with seeded repository state and expected local verification,
then computes route/budget decisions without calling live models. `--live` is
an explicit opt-in runner or reviewer path that records task success, Depone
signed-bundle verification, elapsed time, turns, available token usage,
estimated cost, and model declaration status. A fallback receipt is recorded
only when the adapter exposes an observed model; otherwise the task carries an
explicit unavailable-model receipt. Live output includes advisory per-role/tier budgets
capped by the existing model-policy token, cost, and depth ceilings.
Fallback observation is not complete, so multi-candidate fallback remains
disabled. The measurement and budget advisory
are not proof, verifier truth, a benchmark claim, approval, or assurance, and
cannot change an evidence verdict.

`orro flowplan --profile <profile>` compiles a deterministic rolepack/workflow
plan for `code-change`, `review-only`, `verification-only`, `docs-change`, or
`release-readiness`. The plan is an `orro-workflow-plan` intent artifact, not
evidence. Roles do not create assurance by existing. `proofrun` is the first
execution phase, `proofcheck` is the verifier phase, and `handoff` is review
packaging only.

`orro proofrun --workflow-plan <path>` first applies a workflow phase gate. The
plan must allow `proofrun` and include a witnessd engine call that executes but
does not verify. If the plan does not allow the phase, proofrun fails before it
creates a run directory. When the phase is allowed, proofrun records
`workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json` in the run directory. The binding and role
dispatch are review context only: actual execution proof still begins with
proofrun evidence, Depone proofcheck still decides what the bytes support, and
neither artifact is proof, verification, approval, or assurance. `proofcheck`
and `handoff` preserve these references when they are present.

`orro flowplan --role-lanes-out <path>` writes an `orro-role-lane-plan` that
maps executable workflow roles to witnessd team lanes. The default lane adapter
is deterministic `shell`; live model adapters are not used by default.
For `code-change`, `--write-scope '<glob>'` (repeatable) is a bounded write
scope input that generates the role capability directly instead of requiring a
prebuilt rolepack. It is never inferred or defaulted; without `--write-scope`,
`--rolepack`, `--rolepack-file`, or `--team`, code-change role-lane compilation
still fails closed.
`orro proofrun --workflow-plan <path> --role-lane-plan <path>` validates that the
role-lane plan is bound to the workflow plan, that proofrun is allowed, and that
execution is explicitly allowed before creating a run directory. It then reuses
existing witnessd team execution, fan-in, observer, and ledger machinery.
Role-lane plans are executable intent, not proof. `review-only` and default
`release-readiness` role-lane plans cannot launch proofrun. `verification-only`
role-lane plans compile declared shell check lanes (`flowplan --check`,
repeatable) with an empty write region; proofrun executes those checks under
observation, a non-zero check exit blocks the lane, and any mutation is
falsified by Depone (`ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`). Formal `orro
handoff` still requires a passing bound `proofcheck-verdict.json`.

`orro next <run-dir> --home .witnessd --json` is the non-executing continuation
gate before future `orro auto`. It reads persisted artifacts such as workflow
bindings, role-lane bindings, role dispatch, `team-ledger.json`,
`proofcheck-verdict.json`, and `orro-handoff.json`; then it reports the safest
next allowed action. It does not run proofcheck, execute workers, retry lanes,
repair evidence, approve merge, verify evidence, or raise assurance. Decisions
include `needs-proofcheck`, `ready-for-handoff`, `complete`, `blocked`,
`evidence-pending`, and `invalid-run-dir`. Role status is derived from observed
artifacts only and is not proof. `complete` requires `orro-handoff.json` to be
bound to the current run directory and current `proofcheck-verdict.json`; stale
or copied handoff artifacts block continuation.

Runtime hardening fails closed on malformed, stale, copied, or unbound critical
artifacts. Corrupted workflow bindings, role-lane bindings, role dispatch, team
ledgers, team-ledger verdicts, proofcheck verdicts, or handoff packages block
`next`, `report`, and `auto` instead of being treated as proof or success.

`orro report <run-dir> --home .witnessd --json` is the human-facing compression
layer over a run directory. It summarizes observed workflow, role-lane,
execution, proofcheck, handoff, continuation, auto, and optional workstyle
artifacts; then it states the next safe action and reviewer focus. It helps
non-developers and reviewers understand what happened and reduces artifact
fatigue. It does not execute, verify evidence, run proofcheck, package handoff,
approve merge, raise assurance, replace proofcheck, or replace human review.

`orro auto --dry-run <run-dir> --home .witnessd --json` consumes `orro next`
state and emits an `orro-auto-plan` with the exact command it would run next,
such as proofcheck or handoff. It does not run that command, call Depone, launch
workers, write proofcheck verdicts, write handoff packages, mutate worktrees,
approve merge, verify evidence, or raise assurance. The auto-plan is
recommendation context only, not proof. Broader autonomous `orro auto` remains
future work and must stay gated by continuation decisions.

`orro auto --once <run-dir> --home .witnessd --json` re-checks continuation
state and executes at most one safe next step. In v0, the only allowed steps are
proofcheck for `needs-proofcheck`, handoff for `ready-for-handoff`, and no-op for
`complete` after the handoff binding is checked. It never launches proofrun or
workers, calls live models or MCP, repairs artifacts, retries lanes, resumes
lanes, approves merge, or raises assurance. When it runs proofcheck,
verification is delegated to Depone. The `orro-auto-receipt` is orchestration
metadata only, not proof or verifier truth.

`orro auto --until-complete <run-dir> --home .witnessd --max-steps 2 --json` is
bounded post-run automation. It re-checks continuation state before every step
and may run only proofcheck and handoff. It never launches proofrun or workers,
calls live models or MCP, repairs artifacts, retries or resumes lanes, approves
merge, or raises assurance. It stops on blocked, evidence-pending,
invalid-run-dir, max-steps, or complete. The `orro-auto-session` is orchestration
metadata only, not proof or verifier truth.

The separate `Moonweave-Systems/ORRO` repository now exists for distribution
needs: marketplace manifests, host-specific plugin packaging, version locking,
examples, product docs, and end-to-end integration tests. That repo is a wrapper
and distribution repo, not a third engine; it must not duplicate witnessd
runtime logic or Depone verifier logic.

The concrete migration trigger, allowed standalone skeleton, version lock
format, engine boundary contract, and e2e smoke contract are recorded in
[`docs/orro-productization-roadmap.md`](docs/orro-productization-roadmap.md).

## Operating model

ORRO is an evidence-backed agent-team operating surface. The normal loop is:

```text
scout -> flowplan -> proofrun -> proofcheck -> handoff
```

The scout step uses progressive disclosure instead of loading a whole repository
into one model context. It produces:

- `repo-profile.json`
- `context-pack.json`
- `discovery-notes.md`
- optional `skillpack-lock.json`

Runnable lanes may include:

- `verification-recipe.json` for intended checks,
- `verification-receipt.json` for actual command execution,
- `mcp-tool-receipt-*.json` for declared external tool bridge calls,
- `pr-handoff.json` for maintainer review.

Scout does not write `verification-receipt.json`; it has not run the recipe.
Depone proofcheck treats a scout-only artifact directory as planning evidence,
not proof of execution.

`flowplan` remains strictly plan-only. With a profile, it may write an
`orro-workflow-plan` that maps the goal to roles, phases, engine calls, required
gates, and forbidden assurance sources. It does not run workers, call live
models, call Depone verification, mutate worktrees, approve merge, or raise
assurance. Broader autonomous `orro auto` and `orro ultra` remain future work.

The optional workflow-plan binding connects that intent artifact to later
proofrun evidence by hash. Phase gates prevent using a plan for a phase it does
not allow. `workflow-role-dispatch.json` maps workflow roles to actual or
pending engine phases and may reference `team-ledger.json`; it does not let role
names count as evidence. A `review-only` profile is review intent only; actual
`orro handoff` still requires a passing bound `proofcheck-verdict.json`.

Depone decides what these bytes support. Skill text, MCP output, IDE terminals,
tmux panes, and session transcripts are not verdicts by themselves.
The handoff step packages reviewed evidence only after an explicit passing
`proofcheck-verdict.json` bound to the current evidence snapshot; it does not
verify evidence, approve merge, or raise assurance.

## Setup details

Prerequisites:

- Python 3.10 or newer
- `git`
- `openssl`
- a local Depone checkout or provisioned Depone pin

From a checkout with Depone next to witnessd:

```bash
cd witnessd
python3 -m orro setup --home .witnessd --depone-root ../depone --json
python3 -m orro doctor --home .witnessd --json
python3 -m witnessd run "write two independent files" --repo . --home .witnessd
python3 -m witnessd verify .witnessd/runs/<run-dir> --home .witnessd
```

The `run` command prints JSON. Use its `run_dir` field for the verify step:

```bash
run_json="$(python3 -m witnessd run "write two independent files" --repo . --home .witnessd)"
run_dir="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$run_json")"
python3 -m witnessd verify "$run_dir" --home .witnessd
```

On a runner machine without a local Depone checkout, `orro setup` provisions the
pinned verifier into `.witnessd/depone-pinned` and records that setup-time
network use:

```bash
python3 -m orro setup --home .witnessd --json
```

For the same path as CI:

```bash
WITNESSD_DEPONE_ROOT=../depone scripts/quickstart_check.sh
```

Expected output:

```text
quickstart_check: pass
```

## What the commands do

`orro init` delegates to witnessd initialization/provisioning and creates:

- `.witnessd/config.json`
- `.witnessd/provision.json`
- `.witnessd/keys/`

The provision record pins the local Depone checkout by git commit and records the
witnessd commit. Setup may use network only when explicitly allowed by the
operator. Runtime and verify commands do not fetch or install.

`witnessd run "<goal>" --repo <path>` uses the W18 quota-free shell path by
default. It creates a run directory containing:

- optional `workflow-plan.json`, `workflow-plan-binding.json`, and
  `workflow-role-dispatch.json`
- `sealed-plan.json`
- `dispatch-log.jsonl`
- lane evidence directories
- `team-schedule-receipt.json`
- `team-ledger.json`
- `team-ledger-verdict.json`

`witnessd verify <run-dir>` validates the pinned Depone record, invokes Depone
through `python3 -m depone team-ledger`, and rewrites
`team-ledger-verdict.json` from the run bytes.

`witnessd proofcheck <run-dir> --out <run-dir>/proofcheck-verdict.json`
delegates to Depone's proofcheck path and writes the public ORRO verdict artifact
required by `handoff` / `orro handoff`. A missing, malformed, unreadable, or
non-pass `proofcheck-verdict.json`, or one copied from another evidence snapshot,
blocks handoff and does not write `orro-handoff.json`.

`python3 -m orro <subcommand>` and the witnessd-provided `orro` console script
are deprecated compatibility shims. They warn on stderr, delegate to the same
ORRO parser, and will be removed in the next major witnessd release. The
standalone `Moonweave-Systems/ORRO` package now owns the product `orro` command
and distribution docs.

## Session skill

This repo ships two in-session guidance files:

- `SKILL.md` for host skill installation
- `AGENTS.md` for Codex sessions

Both instruct the session agent to scout when useful, design lanes, run witnessd,
then report the Depone verdict. `team-ledger-verdict.json` records the proofrun
team-ledger check; `proofcheck-verdict.json` is the explicit public verdict
artifact required before handoff. A session transcript or lane self-report is not
a verdict, and a self-declared success claim remains evidence-pending until
Depone re-derives the run bytes.

## Auditor path

An auditor does not need witnessd to execute anything. Given a run directory and
Depone:

```bash
python3 -m depone team-ledger \
  --ledger <run-dir>/team-ledger.json \
  --base-dir <run-dir> \
  --out <run-dir>/team-ledger-verdict.json \
  --json
```

Depone verifies from persisted bytes. It does not run lanes. For scope-style
checks, it re-derives consistency between sealed declarations and sealed
observations; it does not observe the live filesystem directly.

## Phase 0 evidence limitations

The current Phase 0 safety patch records known evidence-substrate limitations
instead of upgrading trust claims. Codex JSONL capture, predeclared write paths,
preflight fail-closed checks, and operator-key overwrite protection are release
safety controls. They do not prove code correctness, complete artifact binding,
or full tamper resistance. Same-size same-mtime content changes, runlog
chain-hardening, and eventlog scaling remain Phase 1 evidence-core work until
the redesign gate closes.

## Development checks

From the Moonweave workspace:

```bash
cd depone
python3 -m unittest discover -s tests
cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for script in scripts/revalidate_*.py; do
  PYTHONPATH=../depone python3 "$script"
done
scripts/quickstart_check.sh
```
