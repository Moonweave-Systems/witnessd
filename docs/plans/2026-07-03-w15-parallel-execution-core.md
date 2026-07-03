# W15 — Parallel Provable Execution Core (skeleton → muscle)

**North star:** witnessd is an agent runtime where *done is provable by
construction*. Its one honest gap as a runtime: team fan-in executes lanes
**sequentially** (`fanin.run_team` is a `for` loop) and true multi-agent
parallelism has never been demonstrated. W15 retires that gap: N lanes run
**genuinely concurrently**, each in provable isolation, and the *concurrency
itself* becomes observer-signed evidence that Depone re-derives from bytes.
This is the capability no incumbent runtime (self-reporting orchestrators)
has: not "we ran agents in parallel, trust us" but "here are the signed bytes
that prove N isolated agents ran concurrently and what each one touched."

## Current state (measured, 2026-07-03, HEAD 4c1c8d2)

- `witnessd/fanin.py:65` — lanes execute in a sequential `for spec in
  lane_specs` loop; `run_adapter_lane` blocks until the adapter exits.
- `witnessd/supervisor.py` — Popen-based supervised spawn with process-group
  handling and `wait()`; used for liveness (W2), not for team lanes.
- `witnessd/killswitch.py` + `witnessd/process_identity.py` — SIGTERM/SIGKILL
  with PID-identity binding (`/proc` on Linux, `ps` fallback elsewhere).
- W13 gave per-lane isolated state roots (`_team_run_lane_state_root`) and the
  multi-codex guard `ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED` (`__main__.py:601`)
  which *blocks* unisolated multi-codex rather than enabling parallel runs.
- `witnessd/planner.py::seal_plan` enforces region-disjoint lane packets.
- `witnessd/lock.py::OwnershipRegistry` claims regions up front (serialized in
  the parent) — already concurrency-safe by construction.
- Depone `team_ledger` validates lanes independently and requires a
  merge_receipt only for overlapping touched files; it has **no concept of
  execution concurrency** — nothing in the contract says lanes overlapped in
  time.

## Global consensus review (2026-07, researched before finalizing)

This design was checked against how the strongest teams build the same
pieces. Where consensus exists we follow it; where we deviate we say why.

1. **Worktree-per-agent isolation is the industry pattern, not our
   invention.** Claude Code subagents (`isolation: worktree`), incident.io's
   4–5 routine parallel agents, JetBrains/VS Code first-class worktree
   support — all converge on "one worktree per parallel agent" (Augment
   Code guides, 2026). Our per-lane worktree model is on-consensus; keep it.
2. **Signed execution attestation has a consensus format we already use.**
   CNCF **in-toto Witness** wraps process execution and emits in-toto/DSSE
   attestations verified by policy. witnessd's bundles are already
   in-toto/DSSE — stay on that rail (no new envelope formats). Witness also
   shows the eventual need for a *policy layer* (their Rego policies ≈ our
   hardcoded revalidators); a declarative policy layer is future work, not
   W15.
3. **Structured concurrency is the consensus semantics for "spawn N,
   supervise, cancel".** Trio nurseries / Python TaskGroups / Java JEP 533 /
   Erlang supervision trees agree on: children are owned by a scope; the
   scope never exits while children live (**no orphans**); fail-fast means
   **cancel siblings, then WAIT for them to actually exit** before
   concluding; cancellation is a first-class recorded event. W15 adopts
   these as hard requirements (see §3). Our two stop rules map to Erlang
   strategies: `all write lanes pass or block` ≈ one-for-one (isolate
   failure), `fail-fast` ≈ one-for-all (cancel all).
4. **Durable execution consensus (Temporal/Restate) = journal + replay.**
   The industry standard for crash recovery is an event journal where
   completed steps are never re-executed and incomplete ones resume. Our
   append-only hash-chained runlog *is* a journal; W15 ships the honest
   subset (`resume-audit`: classify from surviving bytes, never fabricate),
   and **journaled replay-resume is explicitly W17**, not skipped silently.
5. **Where we are genuinely novel (no prior art found):** observer-derived
   **concurrency proof** (max_overlap re-derived by an independent,
   non-executing verifier from signed intervals — Witness/OVERT/OTel all
   attest *that* things ran, none prove *parallel* execution from bytes),
   and the assurance ladder applied to a parallel team run. OVERT 1.1
   (2026-06, Glacis) attests *governance controls executed* at the runtime
   boundary — adjacent but different axis from "work completion provable
   from bytes". This receipt is the wedge; build it exactly as specced.
6. **Observability continuity:** OTel GenAI semantic conventions are the
   consensus for agent telemetry; `build_bundle` already carries optional
   `otel_spans`. The schedule receipt should populate per-lane spans so the
   same evidence is consumable by standard OTel tooling. Optional, cheap,
   on-consensus.

## Design

### 1. Lane executor process model (parallelism via process boundary)

Replace in-loop blocking execution with **one child process per lane**:

```
witnessd lane-exec --spec <lane-spec.json> --out <lane-evidence-dir> ...
```

- The parent (orchestrator) claims all regions up front (existing
  OwnershipRegistry, unchanged), prepares each lane's worktree + isolated
  state root (existing W13 helpers), then **spawns all runnable lanes as
  concurrent child processes** via the existing `supervisor` primitives
  (process groups, no double-reaping).
- Each child runs exactly what `_run_adapter_lane` runs today (same evidence,
  same per-lane runlog, same emitter path) — the evidence *content* per lane
  is unchanged. Children never write to the parent's runlog or to each
  other's directories (parent-owned team runlog; per-lane runlogs are already
  per-directory).
- Concurrency cap: `--max-parallel N` (default: number of lanes, bounded by
  `os.cpu_count()`); a simple parent poll loop (`Popen.poll()` + small sleep)
  schedules pending lanes as slots free. **stdlib only. No threads required**
  for the core loop (threads may be used only if transcript plumbing forces
  it; prefer file-based transcripts as today).
- The multi-codex guard changes meaning: multiple codex lanes are **allowed
  when and only when each lane has an isolated state root** (W13); the
  unisolated case still errors with the same code. The guard moves from "block
  parallelism" to "require isolation for parallelism."

### 2. Provable concurrency: the team-schedule receipt (new contract, Depone PR first)

Self-reported "we ran in parallel" is exactly what this product exists to
kill. Concurrency must be observer-derived:

- The **parent orchestrator** (which workers cannot influence — worker
  self-seal is already forbidden) records, per lane: `spawned_at` /
  `exited_at` (UTC + `time.monotonic_ns()` pairs), `pid`, `pid_start_token`
  (existing process_identity binding), `exit_code`, `lane_id`,
  `worktree`, `state_root`.
- These are assembled into a **`team-schedule-receipt`** record, signed with
  the operator key via the existing DSSE path (`build_bundle` — no new
  signing scheme), and referenced from the team ledger.
- **Depone side (contract — its own PR first, per the moonweave protocol):**
  - New validator `validate_team_schedule_receipt` in
    `depone/agent_fabric/` (additive; no change to
    `ingest_signed_evidence_bundle`, no change to existing kinds):
    - schema/kind checks; monotonic sanity (`exited >= spawned` per lane;
      monotonic pairs consistent with wall-clock ordering);
    - `pid_start_token` present per lane (or explicitly
      `"unavailable"` — never fabricated);
    - **overlap derivation**: from the intervals alone, compute the maximum
      number of simultaneously-live lanes (`max_overlap`). The receipt does
      NOT self-declare parallelism; Depone derives it from the intervals.
  - Team ledger gains an optional `schedule_receipt` path field (additive,
    like `worktree_receipt`); when present, the ledger verdict includes the
    derived `max_overlap`. Absent ⇒ verdict unchanged (backward compatible —
    all existing fixtures stay valid).
- Honest boundary: timestamps come from the parent process on one host. This
  proves *process-level concurrency under the orchestrator's clock*, not
  distributed simultaneity. Say exactly that in the receipt's `boundary`
  note.

### 3. Failure isolation and stop rules under concurrency (structured-concurrency semantics)

Hard requirements, adopted from the structured-concurrency consensus
(nurseries / supervision trees):

- **No orphans, ever.** The orchestrator is a nursery: it must not return
  while any lane process lives. Every exit path (success, failure,
  fail-fast, operator kill, exception in the parent loop) reaps all
  children before the team run concludes. The schedule receipt is only
  written after all lanes have actually exited.
- One lane failing/blocked must not kill siblings (`all write lanes pass or
  block`, one-for-one): parent records the exit, continues supervising
  others, ledger records per-lane `verification_state` exactly as today.
- `"fail-fast"` (one-for-all): on first lane failure, **cancel siblings via
  the existing killswitch (PID-identity bound — never kill a reused PID),
  then WAIT for each cancelled lane to actually exit**, then conclude.
  Cancelled lanes are recorded `blocked` with reason
  `ERR_TEAM_LANE_CANCELLED_FAIL_FAST` and their real exit is in the
  schedule receipt. Cancellation is a recorded first-class event, not an
  absence of data.
- Killswitch works across the whole team: `witnessd team kill --state-root`
  terminates all live lanes (existing killswitch per lane), and the schedule
  receipt records the kill as the exit cause — a killed run must still
  produce a valid, honest ledger (blocked lanes, not vanished lanes).

### 4. Durability / crash honesty

- If the parent crashes mid-run, per-lane evidence remains on disk and each
  lane's own runlog hash chain stays intact. A `team resume-audit` command
  reconstructs what is *provable* from surviving bytes (which lanes have
  complete evidence, which are indeterminate) — it never fabricates
  completion for indeterminate lanes; they are `blocked:
  ERR_TEAM_LANE_INDETERMINATE_PARENT_CRASH`.
- W15 ships audit-only recovery. Full **journaled replay-resume**
  (Temporal-style: re-enter the run, skip lanes with complete evidence,
  re-execute incomplete ones) is **W17** — planned, not silently dropped.

### Out of scope (unchanged commitments)

- **Merge lanes / overlapping regions** (Depone merge_receipt exists but the
  witnessd bridge stays unwired) — that is W16, after W15 lands.
- Agent personas, transparency log / AAL-4, MEASURE — permanent/major-track
  exclusions per SPEC.
- Distributed (multi-host) parallelism — single-host process parallelism
  only.

## Acceptance bar (committed fixture + revalidator, quota-free)

1. `scripts/revalidate_w15.py` + committed fixture `fixtures/w15/`:
   a 3-lane team run using **shell/fake adapters (no codex quota)** with
   deliberately overlapping sleep windows, where Depone re-derives from
   bytes: all 3 lanes pass, regions disjoint, schedule receipt verifies, and
   **derived `max_overlap >= 2`** (true concurrency proven from intervals,
   not asserted).
2. Failure-isolation test: 3 concurrent lanes, one fails → siblings complete,
   ledger honest (1 blocked/2 pass).
3. Fail-fast test: `stop_rule="fail-fast"` → siblings killed via killswitch,
   recorded blocked with the cancel reason, ledger + schedule receipt still
   valid.
4. Parent-crash test: simulate parent death (kill the orchestrator in test),
   `team resume-audit` classifies lanes honestly; no fabricated completions.
5. Existing suites: full witnessd + Depone tests green on Linux **and**
   macOS (both are first-class now); all existing revalidators (w1–w14
   set, key_rotation, w6_keyless, v2_demo) unchanged and green; gate stays
   open; `ingest_signed_evidence_bundle` diff 0.
6. Follow-up (operator, paid, not part of this wave's bar): one live
   2-codex-lane parallel run on a real repo — the first *live* multi-agent
   parallel proof. Tooling from this wave must make that a single command.

## Sequencing (the moonweave cross-repo protocol, non-negotiable)

1. **Depone PR first**: `team-schedule-receipt` validator + ledger
   `schedule_receipt` field + conformance fixtures (additive only).
2. Depone merged/pushed → witnessd implements lane-exec, parallel
   orchestrator, schedule receipt emission, stop rules, resume-audit.
3. witnessd revalidate_w15 fixture + full matrix → land.
