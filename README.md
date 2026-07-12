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
python3 -m orro init --home .witnessd --depone-root ../depone
python3 -m orro doctor --home .witnessd --json
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
python3 -m orro advise "write two independent files" --repo . --home .witnessd --json
python3 -m orro scout "map the repo before planning" --repo . --home .witnessd
python3 -m orro flowplan "write two independent files" --root . --profile code-change --out .witnessd/workflow-plan.json --role-lanes-out .witnessd/role-lane-plan.json
run_json="$(python3 -m orro proofrun "write two independent files" --repo . --home .witnessd --workflow-plan .witnessd/workflow-plan.json --role-lane-plan .witnessd/role-lane-plan.json)"
run_dir="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$run_json")"
python3 -m orro next "$run_dir" --home .witnessd --json
python3 -m orro auto --dry-run "$run_dir" --home .witnessd --json
python3 -m orro auto --until-complete "$run_dir" --home .witnessd --max-steps 2 --json
python3 -m orro report "$run_dir" --home .witnessd --json
python3 -m orro next "$run_dir" --home .witnessd --json
```

`orro team init` only writes readiness configuration (`.orro/team.json`). It is
not execution, verification, proof, or assurance. `orro team go` is the ergonomic
wrapper for the longer `flowplan -> proofrun -> proofcheck -> report` path: it
threads the intermediate paths, passes the user's task text into the runner lane
prompt, writes `proofcheck-verdict.json`, and exits non-zero when the lane did no
work or Depone does not pass the evidence.

```bash
python3 -m orro team init --role runner:codex:gpt-5.5 --write-scope orro/task-output.txt --yes
python3 -m orro team go "Create orro/task-output.txt with the exact line: hello ORRO" --repo . --home .witnessd --json
```

When `--profile` is omitted, `team go` calls `orro advise` and uses the
recommended profile. When `--team` is omitted, it selects the deterministic
default rolepack for that profile. Explicit `--profile` and `--team` always win.
The run writes `moonweave-routing-decision.json` so the advisory routing choice,
rule matches, selected profile, and selected rolepack are visible. That artifact
cannot change the evidence verdict and is not proof, approval, or assurance.

The `proofrun` command prints JSON. Use its `run_dir` field for the proofcheck
and handoff steps. `proofcheck` must write an explicit `proofcheck-verdict.json`
before packaging the handoff:

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

witnessd may emit self-declared runtime facts and `DELAYED_NOTARY` style
post-hoc records, but those records do not upgrade trust. A2 requires a
dedicated observer uid, a separate runner, and observer-owned evidence paths that
are not writable by the runner. Depone decides what the persisted bytes support.
For role-capability write scope, Depone can re-derive whether sealed touched-file
observations fit the sealed declared scope. That is tamper-evident consistency of
persisted evidence, not ground-truth proof of every host filesystem side effect.

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
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo exploration, repo profile, context pack, and discovery notes |
| `flowplan` | plan-only workflow design and ORRO workflow compiler surface |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation gate over persisted run artifacts |
| `orro report` | human-facing summary of observed ORRO artifacts and next safe action |
| `orro auto --dry-run` | non-executing automation planner that recommends the next command |
| `orro auto --once` | one-step executor for proofcheck or handoff only |
| `orro auto --until-complete` | bounded post-run loop over proofcheck and handoff only |
| `orro team init` | scaffold `.orro/team.json` rolepack readiness config; not proof or assurance |
| `orro team go` | one-command flowplan/proofrun/proofcheck/report wrapper; reports Depone verdict |
| `orro skillpack` | knowledge-as-code and progressive-disclosure support |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro auto` | future broader resume/continuation loop behind evidence gates |
| `orro ultra` | future high-autonomy profile with stricter gates |

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

Public ORRO setup starts with `orro init`, which delegates to existing witnessd
initialization/provisioning and creates readiness metadata such as
`.witnessd/provision.json`. It does not run ORRO Flow work, verify evidence,
approve merge, or raise assurance. For local development, provide an explicit
Depone checkout:

```bash
python3 -m orro init --home .witnessd --depone-root ../Depone
python3 -m orro doctor --home .witnessd --json
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
```

`orro doctor` checks readiness, not evidence truth. An engine lock records the
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

`python scripts/check_orro_product_reality.py` validates local dogfood scenarios
for ORRO usefulness: smallest safe workflow, waste avoidance, proofcheck/handoff
gate integrity, artifact fatigue reduction, and clear next action. It is not
proof, verification, telemetry, a benchmark claim, approval, or assurance.

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
`orro proofrun --workflow-plan <path> --role-lane-plan <path>` validates that the
role-lane plan is bound to the workflow plan, that proofrun is allowed, and that
execution is explicitly allowed before creating a run directory. It then reuses
existing witnessd team execution, fan-in, observer, and ledger machinery.
Role-lane plans are executable intent, not proof. `review-only`,
`verification-only`, and `release-readiness` role-lane plans do not launch
proofrun. Formal `orro handoff` still requires a passing bound
`proofcheck-verdict.json`.

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
python3 -m orro init --home .witnessd --depone-root ../depone
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

On a runner machine without a local Depone checkout, setup can provision the
pinned verifier into `.witnessd/depone-pinned` and record that setup-time network
use:

```bash
python3 -m orro init --home .witnessd --allow-network
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
ORRO parser used by `python3 -m witnessd orro ...`, and will be removed in the
next major witnessd release. The standalone `Moonweave-Systems/ORRO` package now
owns the product `orro` command and distribution docs.

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
