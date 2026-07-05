# witnessd SPEC3 - ORRO Runtime Spec

Status: source-of-truth spec, 2026-07-04.

One-line decision: **witnessd executes and emits evidence; Depone verifies the
bytes; ORRO exposes the workflow.** Moonweave is the publisher/account name, not
the product surface.

This file is the only top-level witnessd product/runtime authority. `SPEC.md`,
`SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`,
`AGENTS.md`, fixture notes, and release notes are derived, wave-specific, or
historical. If they conflict with this file, this file wins. Depone's verifier
contract remains authoritative in the Depone repo at `docs/spec.md`.

---

## 1. Product names

| Name | Surface | Meaning |
| --- | --- | --- |
| Moonweave / Moonweave Systems | publisher/account | GitHub org, operator, and release namespace. Not the product UX name. |
| ORRO | flagship product/tool | Observed Run & Review Orchestrator. A goal becomes an evidence-backed workflow. |
| `orro` | primary command/skill | User-facing command and host skill surface. |
| ORRO Flow | workflow loop | `scout -> flowplan -> proofrun -> proofcheck -> handoff`. |
| `orro init` | setup surface | Create witnessd readiness/provision metadata for ORRO; not proof or assurance. |
| `orro scout` | read-only exploration | Build repo profile, context pack, and discovery notes before planning. |
| `flowplan` | plan-only alias | Build or validate a workflow plan, including rolepack/workflow profiles, without running workers. |
| `proofrun` | precise run alias | Execute with observer-signed evidence. Kept for technical invocation accuracy. |
| `proofcheck` | verifier alias | Re-check existing evidence bytes offline. |
| `orro handoff` | maintainer handoff | Bind code changes to an explicit passing proofcheck verdict for human review; not merge approval. |
| `orro skillpack` | knowledge-as-code support | Manage SKILL/CLAUDE/rule/MCP bundles with progressive disclosure. |
| `orro doctor` | readiness check | Check engines, verifier pin, adapters, MCP availability, keys, and policy gates. |
| `orro auto` | automation mode | Dry-run planner now; future executing continuation behind evidence gates. |
| `orro ultra` | future high-autonomy profile | Same gates as ORRO, but with larger budgets and longer loops. |
| Superflow | historical/compatibility name | Former surface name. Do not use for new public docs except migration notes. |
| witnessd | engine | Runtime, adapters, sessions, worktrees, team orchestration, evidence emission. |
| Depone | engine | Non-executing verifier and evidence-contract authority. |

Naming rule: user-facing names say the job, not the engine or account. `witnessd`
and `Depone` stay as repo/engine names. `Moonweave` stays as publisher/account
credit. New sessions and plugin surfaces should lead with `orro`, `flowplan`,
`proofrun`, and `proofcheck`.

Avoid names that imply trust before the verifier has re-derived the bytes.

Compatibility rule: existing `superflow` commands, fixture paths, or artifact
kinds may remain during migration, but the canonical product name is ORRO and the
canonical workflow name is ORRO Flow.

---

## 2. Canonical document set

Future development should start from this small set:

| Purpose | Canonical document |
| --- | --- |
| witnessd runtime/product architecture | `SPEC3.md` |
| Depone verifier/evidence contract | Depone `docs/spec.md` |
| human quickstart | `README.md` |
| in-session skill guidance | `SKILL.md` |
| in-session Codex guidance | `AGENTS.md` |
| agent/developer orientation | `CLAUDE.md` |
| docs map and legacy policy | `docs/README.md` |

Everything else is legacy, historical, fixture-specific, or wave evidence unless
this file explicitly promotes it. Do not create another product source of truth.
When a decision changes, edit this file first, then update derived summaries.

---

## 3. Final architecture

```text
User / agent host
  Claude Code, Codex, OpenCode, local shell, IDE terminal
        |
        v
ORRO surface
  orro | scout | flowplan | proofrun | proofcheck | handoff | auto | ultra
        |
        +-- witnessd execution plane
        |     planner bridge
        |     scheduler / nursery
        |     lane executor
        |     adapter interface
        |     worktree and state roots
        |     observer and evidence emitter
        |     verification-recipe runner
        |     MCP/tool receipt recorder
        |     run journal
        |
        +-- Depone verification plane
              schemas and error codes
              canonical_hash
              validators
              verification-recipe and receipt checks
              MCP/tool receipt checks
              team ledger verdicts
              policy checks
              offline trust-root checks
```

The engines stay separate because the executor must not be the component that
raises trust. The user-facing install surface should be one product because users
should not have to hand-wire two repositories.

### 3.1 Repository and distribution strategy

Development stays in two engine repositories:

```text
Depone   = verifier engine and evidence contract
witnessd = execution engine and evidence emitter
```

ORRO is one user-facing install, command, and skill. Normal users should not be
told to install separate Depone and witnessd skills for one workflow.

In the near term, the thin ORRO entrypoint lives in the witnessd repo because
ORRO starts execution and witnessd owns execution. `python3 -m orro ...`
delegates to the existing `witnessd orro ...` surface. It is not a standalone
ORRO repository and not a third engine. Depone is consumed as a pinned verifier
dependency.

The current public entrypoints are:

```bash
python3 -m orro init --home .witnessd --depone-root ../Depone
python3 -m orro doctor --home .witnessd --json
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
python3 -m orro scout "inspect repo" --repo .
python3 -m orro flowplan "plan goal" --root .
python3 -m orro flowplan "fix bug in parser" --root . --profile code-change --out workflow-plan.json
python3 -m orro proofrun "fix bug in parser" --repo . --home .witnessd --workflow-plan workflow-plan.json
python3 -m orro proofcheck .witnessd/runs/<run-dir> \
  --home .witnessd \
  --out .witnessd/runs/<run-dir>/proofcheck-verdict.json
python3 -m orro auto --dry-run .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro handoff .witnessd/runs/<run-dir> \
  --out .witnessd/runs/<run-dir>/orro-handoff.json
```

`orro init` is a public setup alias over existing witnessd
initialization/provisioning. It creates readiness/provision metadata such as
`.witnessd/provision.json`; it does not run ORRO Flow work, verify evidence,
approve merge, or raise assurance. If no local Depone root is supplied, existing
witnessd initialization behavior may provision according to its current
configuration; tests and local development should use `--depone-root`.

`python3 -m orro --help` is product-facing and lists only the public ORRO Flow and
support commands: `init`, `scout`, `flowplan`, `proofrun`, `proofcheck`,
`handoff`, `next`, `auto`, `doctor`, and `engine-lock`. It must not promote witnessd
engine-internal commands.
Subcommand behavior still delegates to the witnessd-hosted ORRO surface.

The bare `orro` console script is package metadata for the same module
entrypoint:

```text
orro = orro.__main__:main
```

It must remain an alias layer over the witnessd-hosted ORRO surface, and install
smoke tests must cover help, init, flowplan, engine-lock write/check, and
fail-closed engine-lock behavior.

The engine lock is distribution metadata only. It records pinned engine commits
and can check the current local environment for drift against those commits. A
matching lock means distribution/readiness alignment only. A mismatch is
readiness-blocked, not verifier-refuted. Engine-lock does not verify evidence,
approve merge, raise assurance, or execute workers. The `engine-lock` command may
read the local witnessd git HEAD and the validated Depone pin in
`.witnessd/provision.json`, but it must not fetch network, update Depone, or
duplicate verifier/runtime logic.

`orro doctor` checks readiness, not evidence truth. It may report setup or
engine-lock mismatch as readiness-blocked, but that is not Depone verifier
refutation.

`orro flowplan --profile <profile>` is the deterministic ORRO rolepack/workflow
compiler v0. Built-in profiles are `code-change`, `review-only`,
`verification-only`, `docs-change`, and `release-readiness`. The compiler emits
an `orro-workflow-plan` intent artifact that maps a goal to roles, phases,
engine calls, required gates, and forbidden assurance sources. It does not run
workers, call live models, call Depone verification, mutate worktrees, approve
merge, raise assurance, or turn ORRO into a third engine.

`orro proofrun --workflow-plan <path>` first gates execution against the supplied
workflow plan. The plan must allow `proofrun` and must include a witnessd
`proofrun` engine call that executes but does not verify. If the gate fails,
proofrun fails closed before creating a run directory. If the gate passes,
proofrun records `workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json` in the run directory. The binding records which
workflow the run intended to follow, and role dispatch maps roles to actual or
pending engine phases. These artifacts are not proof that the run followed the
plan, do not override actual execution receipts, do not approve merge, and do
not raise assurance. `proofcheck` and `handoff` may preserve the references for
review context, but Depone still decides what persisted evidence supports.

`orro flowplan --role-lanes-out <path>` writes an `orro-role-lane-plan`
artifact. It maps executable rolepack roles to witnessd team lanes and records
the workflow plan hash it was compiled from. This is executable intent, not
proof. `orro proofrun --workflow-plan <path> --role-lane-plan <path>` checks the
hash binding, the workflow phase gate, and `execution_allowed` before any run
directory is created, then executes through existing witnessd team machinery.
The resulting run may contain `role-lane-plan.json` and
`role-lane-plan-binding.json`; proofcheck and handoff preserve those references
for review context only. `review-only`, `verification-only`, and default
`release-readiness` role-lane plans cannot launch proofrun.

`orro next <run-dir> --home <home> --json` is the non-executing continuation
gate before future `orro auto`. It reads persisted artifacts and reports the
safest next allowed action. It must not run proofcheck automatically, execute
recipes, launch workers, call live APIs or MCP, repair evidence, retry failed
lanes, write handoff, approve merge, verify evidence, or raise assurance.
`needs-proofcheck` means proofcheck is the next safe action.
`ready-for-handoff` means a passing bound proofcheck verdict exists and handoff
may be packaged. `complete` means handoff exists after proofcheck pass.
`blocked` means do not continue without human or verifier intervention. Role
status is derived from observed artifacts only and is not proof.

`orro auto --dry-run <run-dir> --home <home> --json` consumes the continuation
decision and emits an `orro-auto-plan` containing the exact command ORRO would
run next. It may recommend a future proofcheck or handoff command, but dry-run
itself must not call Depone, run proofcheck, execute workers, write a handoff,
repair evidence, retry lanes, mutate worktrees, approve merge, verify evidence,
or raise assurance. The auto-plan is recommendation context only, not proof.
`orro auto` without `--dry-run` must fail closed until executing automation is
implemented as a separate gated mode.

Create a standalone `ORRO` repo only when distribution needs justify it:
marketplace manifests, host-specific plugin bundles, examples, product docs,
engine version locks, and end-to-end integration tests. That repo is a wrapper and
distribution repo, not a third engine. It must not duplicate witnessd runtime
logic or Depone verifier logic.

### 3.2 Global AI coding workflow adoption

ORRO is CLI-first but not IDE-hostile. It is the autonomous background execution
plane for work that is too large, slow, parallel, risky, or audit-sensitive for
an inline IDE edit. Cursor, Windsurf, and similar AI-native IDEs remain useful for
fast local edits and human steering. ORRO owns the slower, evidence-governed
path:

```text
IDE / human edit path
  -> fast local changes, inline edits, tactical refactors

ORRO Flow path
  -> read-only scout
  -> progressive context pack
  -> multi-lane worktree execution
  -> adapter workers
  -> verification recipes
  -> evidence sealing
  -> Depone re-derivation
  -> maintainer handoff
```

Each runnable lane gets an isolated worktree and state root. The operator may
view the run through tmux, Ghostty, WezTerm, IDE terminals, or a future ORRO
dashboard, but those views are monitoring surfaces only. They are not evidence.
The authoritative state is the run journal, schedule receipt, lane receipts, and
Depone verifier output.

ORRO must not dump an entire monorepo into one agent context. The planner and
workers use progressive disclosure:

1. read file tree and repo profile first,
2. use grep, AST/search tools, and dependency hints to narrow scope,
3. write findings to disk after bounded discovery,
4. load only the relevant files into each lane context,
5. keep lane context aligned to declared ownership regions.

Required planning artifacts:

```text
repo-profile.json
context-pack.json
discovery-notes.md
lane-context.json
```

`discovery-notes.md` follows the two-action rule: after two meaningful search or
read actions, the agent records the finding, path, and why it matters. This
prevents long sessions from losing reasoning in model context.

Team knowledge must be stored as files, not repeated in chat. ORRO treats these
as first-class but non-verdict artifacts:

```text
SKILL.md
CLAUDE.md
AGENTS.md
.cursorrules or equivalent IDE rules
orro/skillpacks/*.md
orro/rules/*.md
orro/mcp/*.json
```

A skillpack has short frontmatter for discovery and a body that is loaded only
when relevant. This keeps context small while preserving domain knowledge.

MCP servers and external tools are allowed as tool bridges, not as trust roots.
They may query databases, internal APIs, monitoring systems, issue trackers, or
SaaS systems. Every external tool use that affects a run must produce a receipt.

```text
mcp-tool-receipt.json
  tool_name
  server_id
  invocation_hash
  redacted_input_hash
  output_hash
  captured_at
  policy_flags
```

MCP output is an observed external fact. It is not final trust. Depone may verify
the receipt shape and hashes, but it does not call the MCP server.

Every lane should carry a machine-readable verification recipe when the task can
be checked by commands. The recipe is intent. The receipt is evidence.

```json
{
  "kind": "orro-verification-recipe",
  "schema_version": "1.0",
  "commands": [
    {
      "id": "unit-tests",
      "argv": ["python", "-m", "pytest", "tests/payments"],
      "expected_exit_code": 0,
      "required": true
    }
  ]
}
```

Object kind migration rule: new artifacts should use `orro-*` kinds. Existing
`superflow-*` artifact kinds may remain accepted as compatibility aliases until
fixtures and code have migrated.

witnessd executes or records command receipts. Depone verifies that required
recipes ran, their receipts match, and their exit codes support the claimed
result.

---

## 4. Responsibilities

### 4.1 witnessd owns execution

witnessd owns:

- lane planning bridge from a sealed plan to runnable lane specs,
- worker spawn, supervision, cancellation, and resume,
- session and state roots,
- git worktree creation and cleanup,
- adapter invocation for shell, Codex, Claude Code, OpenCode, and future engines,
- ownership-region enforcement,
- budget, pause, kill, and lifecycle controls,
- observer capture and evidence emission,
- verification-recipe execution receipts,
- MCP/tool receipt recording for declared tool bridges,
- run journal and schedule receipts,
- operator-key signing and optional later anchoring.

witnessd does not issue final trust. It may report lifecycle and
`evidence-pending`; Depone reports what the evidence supports.

### 4.2 Depone owns verification

Depone owns:

- evidence schemas,
- canonical hash convention,
- capture, receipt, isolation, DSSE, evidence-contract, schedule, and ledger
  validation,
- verification-recipe and verification-receipt validation,
- repo-profile, context-pack, and skillpack-lock binding validation,
- MCP/tool receipt validation as observed external facts,
- PR handoff evidence validation,
- verdict/error vocabulary,
- offline re-derivation,
- policy compliance checks,
- offline verification of future keyless/transparency anchoring.

Depone does not spawn workers, mutate active worktrees, execute recipes, or call
MCP servers.

### 4.3 ORRO owns the user surface

The ORRO surface owns:

- one install surface,
- host-native skill/plugin packaging,
- command aliases and UX copy,
- engine version lock,
- environment checks,
- progressive disclosure and skillpack loading,
- run summary rendering,
- selection of `orro`, `orro scout`, `flowplan`, `proofrun`, `proofcheck`,
  `orro handoff`, and automation modes.

The surface may live inside witnessd while it is thin. A future standalone ORRO
repo must remain a wrapper/distribution layer and must not duplicate verifier or
runtime logic. The concrete standalone-repo trigger, allowed skeleton, engine
version lock format, boundary contract, and e2e smoke contract are maintained in
`docs/orro-productization-roadmap.md`.

---

## 5. Agent team operating model

ORRO is not a loose chat swarm. It is a small evidence-governed team system.
Every subagent is either a worker that produces bytes, a coordinator that decides
safe structure, or a verifier that checks persisted artifacts. No subagent is
allowed to certify its own completion.

| Role | Who performs it | Output | Trust boundary |
| --- | --- | --- | --- |
| Operator | human or calling session | objective, risk approvals, final human decisions | may approve gates, cannot create verifier truth |
| Scout | ORRO/session agent | repo-profile, context-pack, discovery notes | planning only |
| Flow planner | ORRO/session agent/future wrapper | sealed plan, lane packets, regions, budgets, stop rules, verification recipes | plan-only |
| Scheduler | witnessd | dispatch events, schedule receipt, run journal | lifecycle only |
| Lane worker | shell/Codex/Claude/OpenCode/custom adapter | code/doc changes, command receipts, touched files | worker output is a claim until observed |
| Review lane | optional model or shell lane | findings, test receipts, suggested repairs | advisory unless captured as evidence |
| Merge lane | witnessd lane after source lanes | merge receipt or conflict bytes | merge is evidence, not silent approval |
| Observer/emitter | witnessd observer path | capture manifests, bundles, ledger artifacts | creates evidence, not final verdict |
| Verifier | Depone / proofcheck | verdict, assurance, blocked/refuted reasons | final evidence interpretation |
| Maintainer handoff | ORRO / operator | orro-handoff artifact | review package, not approval |

Team movement:

```text
user goal
  -> orro scout builds repo profile and context pack
  -> ORRO creates or imports a flowplan
  -> flowplan divides work into lane packets with regions, budgets, tools, dependencies, and verification recipes
  -> witnessd claims regions and starts independent lanes in parallel
  -> each lane works in its own worktree/state root through its adapter
  -> observer/emitter records what happened while the work happens
  -> verification receipts record commands, exit codes, and output hashes
  -> review lanes and tests run as evidence-producing lanes, not as trust authorities
  -> merge lanes reconcile only the regions that actually overlap
  -> proofcheck asks Depone to re-derive what the bytes support
  -> ORRO prepares a handoff package for human review
```

The team is organic because lanes move as soon as their dependencies and region
claims allow it. The system avoids a global barrier unless a merge, review, or
policy explicitly requires one.

ORRO saves time by narrowing context before expensive agent work starts, running
disjoint regions in parallel, isolating true merge conflicts, skipping completed
lanes on resume only when evidence still verifies, and reusing bytes through
`proofcheck` instead of rerunning work just to regain confidence.

---

## 6. ORRO workflows

### 6.1 `orro scout`

Read-only exploration mode.

```text
goal + repo -> repo-profile -> context-pack -> discovery-notes -> no execution
```

Outputs:

- `repo-profile.json`,
- `context-pack.json`,
- `discovery-notes.md`,
- `lane-context.json`,
- `skillpack-lock.json` when local knowledge files are selected,
- `verification-recipe.json` as intended checks only,
- MCP/tool receipts only for declared planning/tool observations,
- `pr-handoff.json` with unresolved planning risks.

`orro scout` must not create a fake `verification-receipt.json`. It did not run
the recipe, so there is no command execution receipt to verify. A scout-only
artifact directory is planning evidence; Depone `proofcheck` must block it until
a later `proofrun` or witnessd execution step emits a verifier-recognized
verification receipt and the other required execution artifacts.

Allowed terminal states: `scouted`, `blocked`, `inconclusive`. It never reports
A1/A2 because no execution evidence exists.

### 6.2 `flowplan`

Plan-only mode.

```text
goal -> plan contract -> lane/region/budget/gate preview -> no execution
```

Outputs include sealed plan, lane packet list, region and overlap analysis,
budget and stop rules, evidence-contract preview, verification-recipe preview,
and skillpack-lock preview. It never reports A1/A2 because no execution evidence
exists.

The workflow compiler profile output is also plan-only. An `orro-workflow-plan`
records which roles are needed, which engine owns each phase, which phases may
execute, which phases may verify, and which artifacts must exist before handoff.
Roles do not create assurance by existing. `proofrun` is the first execution
phase and belongs to witnessd. `proofcheck` is the verifier phase and delegates
to Depone. `handoff` is review packaging only. `doctor` and `engine-lock` are
readiness/distribution checks only. Executing `orro auto` remains future work.

`review-only` remains review intent. If a formal ORRO handoff artifact is
needed, the actual `orro handoff` command still requires a passing
`proofcheck-verdict.json` bound to the current evidence snapshot.
The `review-only` flow does not authorize `proofrun` and does not imply that the
formal `orro handoff` command can run without proofcheck.

### 6.3 `proofrun`

Precise evidence-backed execution alias.

```text
goal or plan -> witnessd run -> evidence tree -> optional Depone verification
```

Before Depone runs, status is `evidence-pending`.

### 6.4 `proofcheck`

Verifier-only alias.

```text
evidence bytes + public key -> Depone -> verifier report
```

Forbidden in this mode: worker launch, model calls, worktree mutation, retry,
repair execution, and live MCP/server/API calls. `proofcheck` is fail-closed.
Missing, empty, malformed, incomplete, or scout-only artifact directories block
instead of passing.

### 6.5 `orro`

Flagship mode.

```text
goal -> scout -> flowplan -> proofrun -> proofcheck -> handoff summary
```

ORRO is the public story: a goal becomes an evidence-backed workflow. It scouts,
plans, runs, seals, checks what the bytes support, and prepares a human handoff.

### 6.6 `orro handoff`

Maintainer handoff mode.

```text
passing proofcheck-verdict.json -> orro-handoff.json -> human review package
```

A handoff binds code changes to evidence. It is not merge approval and does not
raise assurance.

`handoff` and `orro handoff` require an explicit
`proofcheck-verdict.json` in the evidence/run directory with `decision: "pass"`.
The verdict must also contain the ORRO binding for the current evidence
snapshot. The `team-ledger-verdict.json` generated during `proofrun` is not
sufficient by itself. If the proofcheck verdict is missing, unreadable,
malformed, not a JSON object, copied from another evidence snapshot, or has any
decision other than `pass`, handoff fails closed and must not write
`orro-handoff.json`.

This is a packaging gate, not verifier logic. witnessd may read the proofcheck
verdict artifact to decide whether handoff packaging is allowed, but Depone
remains the verifier and witnessd must not reimplement proofcheck.

### 6.7 `orro skillpack`

Knowledge-as-code support mode. Skillpacks, rules, and MCP declarations are
loaded by frontmatter first and full body only when relevant. Skillpacks may guide
planning and execution but do not raise assurance by themselves.

### 6.8 `orro doctor`

Readiness-check mode for engines, verifier pin, adapter availability, MCP bridge
availability, keys, policies, and required local commands. `doctor` may block a
run before execution; it does not prove task completion.

### 6.9 `orro next`

Non-executing continuation/status gate.

```text
run directory -> artifact observation -> next allowed action
```

`orro next` reads workflow plan bindings, role-lane bindings, role dispatch,
team ledger artifacts, proofcheck verdicts, and handoff packages. It does not
call Depone proofcheck; it reports `needs-proofcheck` when verifier truth is
missing. It does not execute, repair, retry, or continue. This gate is the
precondition for future `orro auto`.

### 6.10 `orro auto`

Dry-run automation planner now; long-running automation mode later.

```text
current evidence -> next gate -> auto-plan recommendation
```

`orro auto --dry-run` is non-executing. It consumes `orro next`, writes or
prints `orro-auto-plan`, and recommends the next command without running it.
Executing automation remains future work.

Rules: no continuation after pause, blocked, or refuted without explicit operator
approval; no budget auto-increase; no unverified plan activation; no merge/deploy
approval from witnessd alone.

### 6.11 `orro ultra`

Future high-autonomy profile. It is not a different trust model. It is ORRO with
larger budgets, longer loops, and stricter pause/budget/proofcheck gates.

---

## 7. Evidence layout

A run directory must be archiveable and re-checkable from bytes:

```text
.witnessd/runs/<run_id>/
  run-summary.json
  repo-profile.json
  context-pack.json
  discovery-notes.md
  skillpack-lock.json
  workflow-plan.json
  workflow-plan-binding.json
  workflow-role-dispatch.json
  sealed-plan.json
  verification-recipe.json
  dispatch-log.jsonl
  runlog.jsonl
  lane-*/
    lane-context.json
    capture-manifest.json
    observer-capture.json
    runner-receipt.json
    verification-receipt.json
    mcp-tool-receipt-*.json
    bundle.json
    provenance.json
    worktree-lane-receipt.json
    evidence-next-verdict.json
  team-schedule-receipt.json
  team-ledger.json
  team-ledger-verdict.json
  proofcheck-verdict.json
  pr-handoff.json
  orro-handoff.json
```

Rules:

- private keys stay outside evidence directories,
- host auth/subscription files stay in isolated state roots,
- evidence directories may be archived after secret scan,
- verifier reports are derived and may be regenerated,
- runlog and capture manifests are append-only evidence,
- repo-profile, context-pack, and skillpack-lock are planning evidence,
- workflow-plan, workflow-plan-binding, and workflow-role-dispatch record
  intended workflow context only,
- verification-recipe describes intended checks,
- verification-receipt records what actually ran,
- scout-only directories omit verification-receipt by design and therefore do not
  produce a proofcheck pass,
- mcp-tool-receipt records external tool use,
- proofcheck-verdict records the explicit Depone proofcheck decision and ORRO
  evidence binding required before ORRO handoff,
- pr-handoff and orro-handoff record review packages and are not approval,
- Depone decides which artifacts can support assurance.

---

## 8. Trust and status model

Execution assurance and trust root are separate axes.

Execution assurance:

```text
A0-claims-only
A1-local-observed
A2-isolated-observed
```

Trust root:

```text
operator-key
keyless-anchored        # future W20
transparency-logged     # future W20+
```

Rules:

- A1/A2 are never granted by witnessd alone.
- Operator-key DSSE is report-level provenance; it does not create A3.
- Keyless/transparency anchoring is a future optional enrichment of already-valid
  evidence, not a dependency of capture.
- Depone must be able to verify persisted anchor bytes offline.
- Skill text, MCP output, and session transcripts are not trust roots unless a
  verifier-recognized receipt binds them to evidence.

Keep lifecycle and evidence status separate.

Lifecycle examples:

```text
scouted
planned
running
paused
dead
resumed
finished-emitting
handoff-ready
```

Evidence/verifier examples:

```text
evidence-pending
A0-claims-only
A1-local-observed
A2-isolated-observed
blocked
refuted
inconclusive
pass
```

A run may have `lifecycle=finished-emitting` and
`evidence_status=evidence-pending`. That means the runtime stopped writing, not
that the result is trusted.

---

## 9. Development roadmap

The remaining work is ordered. A wave lands only when its acceptance bar is met
and prior fixture revalidators remain green.

Cross-cutting acceptance requirements:

1. Progressive disclosure: no lane should require whole-repo context when a repo
   profile and context pack can narrow scope.
2. Verification recipes: executable success criteria must be machine-readable and
   receipt-bound.
3. Knowledge as code: domain guidance lives in skillpacks, rules, `CLAUDE.md`,
   `AGENTS.md`, or equivalent files, not only in chat.
4. MCP/tool receipts: external tool outputs are hash-bound observations, not
   trust roots.
5. Checkpoint recovery: scout, plan, run, check, and handoff artifacts must allow
   restart or audit without replaying successful work.
6. Human gate: destructive, production, secret, paid/live, and merge/deploy
   actions require explicit approval unless a future policy says otherwise.

Roadmap:

- W15: parallel provable execution core with schedule receipts.
- W16: merge lanes for overlapping regions.
- W17: journaled replay-resume.
- W18: distribution, session UX, ORRO command/skill bootstrap, scout artifacts,
  skillpack discovery, verification-recipe receipts, `orro doctor`, the thin
  `python3 -m orro` module entrypoint, ORRO engine-lock write/check v0, and the
  `orro` console script alias.
- W18.5: MCP and enterprise tool receipts.
- W17.5: design-to-execute bridge.
- W19: first live multi-agent parallel proof.
- W20: keyless identity and transparency anchoring.
- W21: declarative verification policy layer.
- W22: published contract and conformance kit.

W18 acceptance: quickstart passes, fresh-session skill run works, no manual
PYTHONPATH, no separate Depone/witnessd skill install for normal users, a
quota-free scout fixture includes repo-profile, context-pack, verification-recipe,
and a blocked proofcheck result for scout-only planning evidence, and later
proofrun fixtures pass only after real verification receipts are emitted.

---

## 10. Document legacy policy

Legacy docs are not deleted because they preserve implementation history and
fixture rationale. They are not planning authorities. Any legacy doc that appears
to conflict with this file must be read as historical context until explicitly
promoted here.

Legacy categories:

- `SPEC.md` and `SPEC2.md`: foundation history,
- `docs/plans/*`: wave notes and acceptance evidence,
- `docs/conformance/*`: conformance notes derived from implemented artifacts,
- fixture README files: evidence explanations,
- old release and benchmark docs: historical process artifacts,
- Superflow naming: historical/compatibility product-surface naming superseded by
  ORRO.

New technical design should update this file or Depone `docs/spec.md`; do not add
a new competing architecture document.

---

## 11. Final invariant

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```
