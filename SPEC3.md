# witnessd SPEC3 — Superflow Runtime Spec

Status: source-of-truth spec, 2026-07-04.

One-line decision: **witnessd executes and emits evidence; Depone verifies the
bytes; Superflow exposes the workflow.** Moonweave is the publisher/account name,
not the product surface.

This file is the only top-level witnessd product/runtime authority. `SPEC.md`,
`SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`, `AGENTS.md`,
fixture notes, and release notes are derived, wave-specific, or historical. If
they conflict with this file, this file wins. Depone's verifier contract remains
authoritative in the Depone repo at `docs/spec.md`.

---

## 1. Product names

| Name | Surface | Meaning |
| --- | --- | --- |
| Moonweave / Moonweave Systems | publisher/account | GitHub org, operator, and release namespace. Not the product UX name. |
| Superflow | flagship product/tool | Goal -> plan -> execute -> seal evidence -> verifier summary. |
| `superflow` | primary command/skill | The user-facing command surface. |
| `flowplan` | plan-only alias | Build or validate a workflow plan without running workers. |
| `proofrun` | precise run alias | Execute with observer-signed evidence. Kept for technical invocation accuracy. |
| `proofcheck` | verifier alias | Re-check existing evidence bytes offline. |
| `superflow auto` | automation mode | Resume and continue work behind evidence gates. |
| `superflow ultra` | future high-autonomy profile | Same gates as Superflow, but with larger budgets and longer loops. |
| witnessd | engine | Runtime, adapters, sessions, worktrees, team orchestration, evidence emission. |
| Depone | engine | Non-executing verifier and evidence-contract authority. |

Naming rule: user-facing names say the job, not the engine or account. `witnessd`
and `Depone` stay as repo/engine names. `Moonweave` stays as publisher/account
credit. Sessions and plugin surfaces should lead with `superflow`, `proofrun`,
and `proofcheck`.

Avoid names that imply trust before the verifier has re-derived the bytes.

---

## 2. Canonical document set

Future development should start from this small set:

| Purpose | Canonical document |
| --- | --- |
| witnessd runtime/product architecture | `SPEC3.md` |
| Depone verifier/evidence contract | Depone `docs/spec.md` |
| human quickstart | `README.md` |
| in-session Claude skill guidance | `SKILL.md` |
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
  Claude Code, Codex, OpenCode, local shell
        |
        v
Superflow surface
  superflow | flowplan | proofrun | proofcheck | superflow auto
        |
        +-- witnessd execution plane
        |     planner bridge
        |     scheduler / nursery
        |     lane executor
        |     adapter interface
        |     worktree and state roots
        |     observer and evidence emitter
        |     run journal
        |
        +-- Depone verification plane
              schemas and error codes
              canonical_hash
              validators
              team ledger verdicts
              policy checks
              offline trust-root checks
```

The engines stay separate because the executor must not be the component that
raises trust. The user-facing install surface should be one product because users
should not have to hand-wire two repositories.

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
- verdict/error vocabulary,
- offline re-derivation,
- policy compliance checks,
- offline verification of future keyless/transparency anchoring.

Depone does not spawn workers or mutate active worktrees.

### 4.3 Superflow owns the user surface

The planned wrapper/plugin owns:

- one install surface,
- host-native skill/plugin packaging,
- command aliases and UX copy,
- engine version lock,
- environment checks,
- run summary rendering,
- selection of `superflow`, `flowplan`, `proofrun`, `proofcheck`, and automation
  modes.

The wrapper must not duplicate verifier or runtime logic.

---

## 5. Agent team operating model

Superflow is not a loose chat swarm. It is a small evidence-governed team system.
Every subagent is either a worker that produces bytes, a coordinator that decides
safe structure, or a verifier that checks persisted artifacts. No subagent is
allowed to certify its own completion.

### 5.1 Team roles

| Role | Who performs it | Tools | Output | Trust boundary |
| --- | --- | --- | --- | --- |
| Operator | human or calling session | approvals, budget, repo constraints | objective, risk approvals, final human decisions | may approve gates, cannot create verifier truth |
| Flow planner | Superflow / session agent / future wrapper | `flowplan`, repo inspection, Depone plan validators | sealed plan, lane packets, regions, budgets, stop rules | plan-only, no A1/A2 claim |
| Scheduler | witnessd | ownership registry, nursery, budget, pause/kill, session state | dispatch events, schedule receipt, run journal | lifecycle only |
| Lane worker | shell/Codex/Claude/OpenCode/custom adapter | per-lane worktree, isolated state root, allowed tools | code/doc changes, command receipts, touched files | worker output is a claim until observed |
| Review lane | optional model or shell lane | tests, static checks, read-only audits, diff review | findings, test receipts, suggested repairs | advisory unless captured as evidence |
| Merge lane | witnessd lane after source lanes | git merge/reconcile tools, conflict capture | merge receipt or conflict bytes | merge is evidence, not silent approval |
| Observer/emitter | witnessd observer path | snapshots, receipts, runlog, DSSE, provenance | capture manifests, bundles, ledger artifacts | creates evidence, not final verdict |
| Verifier | Depone / proofcheck | schema validators, canonical hash, signature checks, policy checks | verdict, assurance, blocked/refuted reasons | final evidence interpretation |

### 5.2 How the team moves

```text
user goal
  -> superflow creates or imports a flowplan
  -> flowplan divides work into lane packets with regions, budgets, tools, and dependencies
  -> witnessd claims regions and starts independent lanes in parallel
  -> each lane works in its own worktree/state root through its adapter
  -> observer/emitter records what happened while the work happens
  -> review lanes and tests run as evidence-producing lanes, not as trust authorities
  -> merge lanes reconcile only the regions that actually overlap
  -> proofcheck asks Depone to re-derive what the bytes support
  -> superflow reports lifecycle + verifier status separately
```

The team is organic because lanes move as soon as their dependencies and region
claims allow it. The system avoids a global barrier unless a merge, review, or
policy explicitly requires one.

### 5.3 How Superflow saves time

- Disjoint regions run in parallel instead of serial chat turns.
- Region ownership prevents two workers from wasting time on accidental conflicts.
- Merge lanes isolate only true overlaps, so unrelated lanes do not wait.
- Schedule receipts let Depone prove concurrency after the fact.
- Completed lanes are skipped on resume only when their evidence still verifies.
- `proofcheck` avoids rerunning work just to regain confidence; it reuses bytes.
- Shell/fake lanes support quota-free planning and validation before paid agents.
- Adapter routing keeps engines swappable; the moat is evidence, not model brand.
- Budget, pause, kill, and policy gates stop bad runs early instead of letting
  long automation continue on unverified state.

### 5.4 Subagent tool limits

Lane workers get only the tools declared by their lane packet and adapter. A lane
may use shell, Codex, Claude Code, OpenCode, local tests, or static analysis, but
it cannot write outside its allowed region without producing evidence that Depone
can reject. Review lanes can critique and test, but they do not turn work into
A1/A2. Merge lanes can reconcile conflicts, but their receipts must be verified.

Human approval is required for destructive operations, paid/live adapters when not
pre-approved, production deployment, secret access, broad network use, and any
continuation after blocked/refuted evidence.

---

## 6. Superflow workflows

### 6.1 `flowplan`

Plan-only mode.

```text
goal -> plan contract -> lane/region/budget/gate preview -> no execution
```

Outputs:

- sealed plan,
- lane packet list,
- region and overlap analysis,
- budget and stop rules,
- evidence-contract preview.

Allowed terminal states: `planned`, `blocked`, `inconclusive`. It never reports
A1/A2 because no execution evidence exists.

### 6.2 `proofrun`

Precise evidence-backed execution alias.

```text
goal or plan -> witnessd run -> evidence tree -> optional Depone verification
```

Outputs:

- run directory,
- run journal,
- capture manifests,
- observer captures,
- runner receipts,
- signed bundles,
- worktree receipts,
- team ledger,
- verifier report when Depone is available.

Before Depone runs, status is `evidence-pending`.

### 6.3 `proofcheck`

Verifier-only alias.

```text
evidence bytes + public key -> Depone -> verifier report
```

Forbidden in this mode:

- worker launch,
- model calls,
- worktree mutation,
- retry,
- repair execution.

### 6.4 `superflow`

Flagship mode.

```text
goal -> flowplan -> proofrun -> proofcheck summary
```

Superflow is the public story: a goal becomes an evidence-backed workflow. It
plans, runs, seals, and checks what the bytes support.

### 6.5 `superflow auto`

Long-running automation mode.

```text
current evidence -> proofcheck -> next gate -> witnessd executes one approved step -> new evidence
```

Rules:

- no continuation after pause, blocked, or refuted without explicit operator
  approval,
- no budget auto-increase,
- no unverified plan activation,
- no merge/deploy approval from witnessd alone.

### 6.6 `superflow ultra`

Future high-autonomy profile. It is not a different trust model. It is Superflow
with larger budgets, longer loops, and stricter pause/budget/proofcheck gates.

---

## 7. Evidence layout

A run directory must be archiveable and re-checkable from bytes:

```text
.witnessd/runs/<run_id>/
  run-summary.json
  sealed-plan.json
  dispatch-log.jsonl
  runlog.jsonl
  lane-*/
    capture-manifest.json
    observer-capture.json
    runner-receipt.json
    bundle.json
    provenance.json
    worktree-lane-receipt.json
    evidence-next-verdict.json
  team-schedule-receipt.json
  team-ledger.json
  team-ledger-verdict.json
```

Rules:

- private keys stay outside evidence directories,
- host auth/subscription files stay in isolated state roots,
- evidence directories may be archived after secret scan,
- verifier reports are derived and may be regenerated,
- runlog and capture manifests are append-only evidence.

---

## 8. Trust model

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

---

## 9. Status model

Keep lifecycle and evidence status separate.

Lifecycle examples:

```text
planned
running
paused
dead
resumed
finished-emitting
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

## 10. Development roadmap

The remaining work is ordered. A wave lands only when its acceptance bar is met
and prior fixture revalidators remain green.

### W15 — Parallel provable execution core

One child process per lane with nursery semantics. Observer-derived schedule
receipt lets Depone derive overlap/concurrency from signed intervals.

Acceptance: quota-free multi-lane fixture, derived overlap, failure isolation,
parent-crash behavior, `revalidate_w15.py`.

### W16 — Merge lanes for overlapping regions

Planner accepts overlap only through an explicit merge lane. Merge conflicts are
evidence and yield blocked/refuted verdicts with conflict bytes.

Acceptance: overlap fixture with merge lane, forged merge receipt negative,
`revalidate_w16.py`.

### W17 — Journaled replay-resume

Interrupted runs resume from the journal. Completed lanes are skipped only when
Depone can re-derive their evidence. Incomplete lanes get fresh attempts.

Acceptance: kill-parent-mid-run fixture, tampered-completion negative,
`revalidate_w17.py`.

### W18 — Distribution and session UX

One command initializes witnessd with a pinned Depone. `proofrun`/Superflow
session guidance becomes the primary runner UX. Clean quickstart works on macOS
and Linux.

Acceptance: `scripts/quickstart_check.sh`, fresh-session skill run, no manual
PYTHONPATH, no second hand-wired clone for normal runner use.

### W17.5 — Design-to-execute bridge

Order note: W18 executes before W17.5 because installability is the adoption gate.
After W18, Depone's plan/contract output becomes a sealed witnessd execution
input. Depone then verifies plan conformance from the produced evidence.

Acceptance: multi-lane plan executes, Depone re-derives plan conformance, region
drift negative, `revalidate_w17_5.py`.

### W19 — Live parallel proof

First live multi-agent parallel run with real Codex lanes on a real repository.
Operator-authorized paid run only.

Acceptance: committed flagship fixture, revalidator, honest mileage section.

### W20 — Keyless identity and transparency anchoring

Optional anchor component signs/seals an already-valid evidence bundle with
keyless identity and stores inclusion proof bytes. Depone verifies offline.

Acceptance: fresh machine verifies anchored evidence without an operator public
key; forged anchor negatives fail.

### W21 — Declarative verification policy layer

Depone adds stdlib JSON policies for requirements such as A2-only, overlap
minimums, keyless anchoring, and region boundaries. witnessd can attach a policy
reference to a run.

Acceptance: prior fixtures rechecked through policies; failing policy produces an
honest failure.

### W22 — Published contract and conformance kit

Depone publishes the evidence contract as a versioned spec with conformance
fixtures. External emitters can target the contract without witnessd code.

Acceptance: spec-only emitter conformance test, OTel/OVERT exports validate.

---

## 11. Document legacy policy

Legacy docs are not deleted because they preserve implementation history and
fixture rationale. They are not planning authorities. Any legacy doc that appears
to conflict with this file must be read as historical context until explicitly
promoted here.

Legacy categories:

- `SPEC.md` and `SPEC2.md`: foundation history,
- `docs/plans/*`: wave notes and acceptance evidence,
- `docs/conformance/*`: conformance notes derived from implemented artifacts,
- fixture README files: evidence explanations,
- old release and benchmark docs: historical process artifacts.

New technical design should update this file or Depone `docs/spec.md`; do not add
a new competing architecture document.

---

## 12. Final invariant

```text
Depone verifies; witnessd executes; Superflow exposes the workflow.
```
