# SPEC — Part III: The Endgame

**This document is the final-form specification for the witnessd × Depone
product pair.** Part I (`SPEC.md`) defined the evidence substrate and trust
ladder; Part II (`SPEC2.md`) defined the execution half through v2.0. Part III
defines everything from here to the finished product: the target architecture,
every remaining wave in order with acceptance bars, and **every open decision,
decided**. Nothing below is a menu. Deviate only on a stop-condition
(security finding, contract break, or an acceptance bar that cannot be met
honestly) — and record the deviation here.

---

## 0. Definition of DONE (what "finished" means)

The product is finished when all six hold, each provable, not claimed:

1. **Install:** one command on macOS and Linux gets a working witnessd with a
   pinned Depone. No manual PYTHONPATH, no second clone by hand.
2. **Run:** one command executes a real goal across **N genuinely parallel,
   provably isolated agent lanes** on a real repository.
3. **Prove:** every run emits observer-signed evidence from which Depone —
   non-executing, offline, on any machine — re-derives what happened,
   including *that* the lanes ran in parallel and *what* each touched.
4. **Trust:** a third party who does **not** trust the operator can verify the
   evidence (keyless identity + transparency-log inclusion, verified offline
   from persisted bytes). This is AAL-4 and it is in scope, not deferred
   rhetoric.
5. **Survive:** crashes, kills, and failures always yield honest evidence
   (blocked/indeterminate lanes, never fabricated completions), and
   interrupted runs resume from their journal without re-executing completed
   work.
6. **Interoperate:** the evidence contract is a published, versioned spec;
   evidence exports cleanly to OTel GenAI telemetry and maps to OVERT's
   governance view. Depone verifies; it never executes. Forever.

## 1. Final architecture

```
┌─ witnessd (execution plane — the runtime) ──────────────────────────────┐
│ orchestrator      nursery semantics: claims regions, spawns lane-exec    │
│                   children, no orphans, cancel-then-wait, stop rules     │
│ lane executor     one process per lane: worktree + isolated state root   │
│                   + uid-isolated observer (A2) + adapter                 │
│ adapters          codex / claude / opencode / shell / custom — one       │
│                   crisp interface; engines are swappable commodities     │
│ evidence spine    capture-manifest → runner-receipt → DSSE bundle →      │
│                   team ledger + schedule receipt + worktree receipts,    │
│                   all content-addressed, hash-chained runlog (journal)   │
│ trust anchor      ISOLATED component (see D1): keyless signing +         │
│                   transparency-log submission; never in capture path     │
└──────────────────────────────────────────────────────────────────────────┘
                     │ evidence bytes only (the one coupling)
┌─ Depone (verification plane — non-executing, offline) ──────────────────┐
│ contract SoT      schemas + error codes + canonical_hash                 │
│ validators        capture / receipt / isolation / DSSE / ledger /        │
│                   schedule (max_overlap derivation) / rotation / keyless │
│ policy layer      declarative JSON verification policies (stdlib)        │
│ trust audit       offline verification of keyless identity + Rekor       │
│                   inclusion proofs from persisted bytes                  │
└──────────────────────────────────────────────────────────────────────────┘
```

Trust model, final form: **assurance** (A0 claims / A1 observed / A2
isolated-observed — execution axis) × **trust root** (operator-key →
keyless identity → transparency-logged — verification axis). AAL-4 =
A2 evidence whose signatures a stranger can verify offline.

## 2. Standing decisions (decided now; do not re-litigate)

- **D1 — the no-network invariant, resolved.** The collision between
  "stdlib+openssl, no network" and live keyless is resolved by **splitting
  the invariant by plane**:
  - *Capture and verification stay offline forever.* Depone never makes a
    network call. The evidence-capture path in witnessd never makes a
    network call. An evidence bundle is always valid without any network.
  - *The runtime gains one explicitly-scoped network component*,
    `witnessd/anchor/` — the trust anchor: Sigstore Fulcio (keyless cert)
    + Rekor (transparency log) submission. It is a separate process,
    feature-gated (`--anchor`), allowed to reach only Fulcio/Rekor, and
    runs *after* evidence is sealed (anchoring is an enrichment of an
    already-valid bundle, never a dependency of it). This is the same kind
    of carve-out as the `openssl` CLI: a narrow, auditable exception, not
    an erosion.
  - *Depone verifies anchors offline*: a Rekor inclusion proof is a Merkle
    proof — verifiable from persisted bytes against a pinned checkpoint.
    Online freshness checking is a separate optional tool, not Depone core.
- **D2 — third-party dependency for the anchor.** stdlib cannot do Fulcio
  OIDC + Rekor. The anchor component may use the official `sigstore`
  tooling **as an external CLI invoked by the anchor process only** (like
  openssl), never as a Python import in witnessd core, never required for
  capture or verification. If the CLI is absent, `--anchor` fails closed
  with `ERR_WITNESSD_ANCHOR_UNAVAILABLE` and the run still produces valid
  operator-key evidence.
- **D3 — `ps` starttime resolution.** The macOS second-resolution PID
  binding weakness is encoded into the contract at W20 (when the contract
  is already being extended for anchoring): capture manifests gain
  `process_identity_source: proc-jiffies | ps-lstart | unavailable`, and
  Depone's verdict carries the strength. Until W20 it remains a documented
  limitation. No hidden downgrade.
- **D4 — repo visibility & the broken reverse-conformance CI job.** witnessd
  goes **public at W22** (contract publication makes the runtime publishable;
  the thesis wants scrutiny). Until then, the depone CI
  `witnessd reverse conformance` job authenticates with a fine-grained PAT
  (repo-read only) added at W18. Fix at W18, not before (it blocks nothing).
- **D5 — scope ceilings for 1.0.** Single-host parallelism only (distributed
  is post-1.0). uid isolation is the A2 ceiling (containers post-1.0).
  Personas, MEASURE, dwm dual-engine remain permanently out of scope.
- **D6 — engine strategy.** witnessd does not build an agent loop. Engines
  (codex, claude, opencode) are commodities behind the adapter interface.
  The moat is the evidence spine + verification plane, made too deep to
  absorb: concurrency proof, isolation receipts, offline re-derivation,
  transparency anchoring. Depth is the defense; do not chase engine
  features.
- **D7 — working protocol.** Each wave: Codex implements from this spec →
  Claude verifies adversarially (independent re-execution; no
  report-trusting) → operator reviews diff and pushes (Depone first whenever
  the contract changes — witnessd CI clones Depone main) → CI green → next
  wave begins **without asking**. Operator-only acts, permanently: pushes,
  gate/status changes, operator reviews, paid codex runs.

## 3. The remaining build — every wave to the end

Waves are strictly ordered. Each has an acceptance bar; a wave lands only
when its bar is met and all prior revalidators stay green (the standing
regression floor: full suites both repos, both platforms; every
`scripts/revalidate_*.py` PASS; gate stays open; `ingest_signed_evidence_bundle`
untouched; no quota spent except where a bar explicitly says "operator,
paid").

### W15 — Parallel provable execution core  *(specced: `docs/plans/2026-07-03-w15-parallel-execution-core.md`; in flight)*
One child process per lane; nursery semantics (no orphans, cancel-then-wait
fail-fast); observer-derived **team-schedule receipt**; Depone derives
`max_overlap` from signed intervals — concurrency proven, never
self-declared. **Bar:** committed quota-free 3-lane fixture with derived
overlap ≥ 2 + failure-isolation/fail-fast/parent-crash tests +
`revalidate_w15.py`.

### W16 — Merge lanes (overlapping regions)
Wire the witnessd bridge to Depone's existing `merge_receipt` contract:
planner accepts overlapping regions by emitting an explicit merge lane;
the merge lane runs *after* its source lanes (sequenced by the scheduler,
recorded in the schedule receipt), applies/reconciles their worktree
outputs, and emits a merge receipt Depone validates (it already does —
`validate_team_merge_attempt_receipt`). Conflicts are evidence, not
failures: an unresolvable merge yields `blocked:
ERR_TEAM_MERGE_CONFLICT_UNRESOLVED` with the conflict bytes attached.
**Bar:** committed fixture — two lanes touching one shared file + merge
lane; Depone re-derives pass-with-merge; negative fixture where a forged
merge receipt is rejected; `revalidate_w16.py`.

### W17 — Journaled replay-resume (durable execution)
The hash-chained runlog becomes a true journal (Temporal-consensus
semantics): `witnessd team resume <run-dir>` re-enters an interrupted run,
treats lanes with complete, verifiable evidence as done (never re-executed),
re-executes incomplete lanes into **fresh** lane attempts (attempt N+1 —
prior partial evidence is retained, never overwritten), and the final
ledger records the full attempt history. Resume is itself evidence: a
`resume-receipt` records what was skipped-as-proven vs re-run and why.
Contract addition (Depone PR first): `resume_receipt` (additive).
**Bar:** kill-parent-mid-run fixture that resumes to a complete honest
ledger; a tampered "completed" lane is *not* skipped (re-derivation fails →
lane re-runs); `revalidate_w17.py`.

### W17.5 — Design→Execute bridge (the third face becomes first-class)
*(Order revision 2026-07-04: **W18 executes before W17.5.** Operator-market
signal: the two-repo install friction is the adoption gate ("I wouldn't
install this myself"); installability unblocks external user #1, the design
bridge does not. W18's one-command story is also the answer to that friction:
a runner installs ONE tool — `witnessd init` provisions the pinned Depone
internally — while an auditor installs ONLY Depone to verify bytes without
any runtime. The two-repo split is for the auditor persona and the W20 trust
story; no end user should ever hand-wire both.)*
*(Appended 2026-07-04 per the append-only rule: the design face existed —
Depone's DWM skill + `compile/` — but was unwired to execution, and witnessd's
`plan_heuristic` is a single-lane stub. The division of labor is: Depone =
design + verification (both non-executing judgment), witnessd = execution.)*
Depone (contract PR first, additive): a **workflow-plan contract** — the DWM
design output (phases, workers/lanes, regions, budgets, gates, stop rules)
normalized into a canonical, hashable plan document that `compile/` emits and
a validator checks (structure, region sanity, budget shape; design is
non-executing, so this stays within Depone's invariant). witnessd: replace the
`plan_heuristic` stub — `witnessd run "<goal>" --plan <plan.json>` (or via the
DWM skill emitting the plan) consumes the contract, seals it (existing
`seal_plan`/plan_hash from W11), and dispatches real multi-lane parallel
execution (W15) with merge lanes where regions overlap (W16). Depone's ledger
verdict gains plan conformance: the executed lanes are re-derived **against
the sealed plan** (lanes match, regions match, nothing off-plan) — design
compliance becomes evidence, not intention. **Bar:** committed fixture where
a DWM-designed multi-lane plan executes in parallel and Depone re-derives
both the work AND its conformance to the sealed plan; negative fixture (a
lane not in the plan, or region drift from plan) rejected;
`revalidate_w17_5.py`. Quota-free (shell/fake adapters).

### W18 — Distribution & DX (the tool becomes installable)
- `witnessd init`: one command creates config, keys dir (0600), pinned
  Depone (pip-installs Depone from its repo at a pinned tag into an isolated
  venv, or vendors the pin — installer decides, records both hashes).
- CLI ergonomics: `witnessd run "<goal>" --repo <path>` = plan → parallel
  lanes → evidence → verdict, with today's flags as advanced overrides.
  `witnessd verify <run-dir>` = local Depone re-derivation, one command.
- Quickstart README rewrite (10-minute path from clone to verified parallel
  run), gh release notes for every tag from v2.1.0 forward, man-style help.
- **In-session skill packaging (appended 2026-07-04; this is the primary
  runner UX).** The operator's consumption model is OMX/OMO-style: an agent
  *inside a session* uses witnessd as its team-execution engine. Ship
  `witnessd/SKILL.md` (Claude Code skill): given a goal, the in-session
  agent designs lanes (via the Depone DWM skill or explicit lanes), runs
  `witnessd run`/`team run` for parallel provable execution, and reports the
  Depone-re-derived verdict — never a self-declared DONE. Pair with
  `AGENTS.md` guidance so Codex sessions drive the same CLI. This is the
  thesis applied to sessions: the session agent's "my team did X" becomes
  observer-signed evidence instead of an OMO-style `<promise>VERIFIED</promise>`.
  Bar addition: from a fresh session with the skill installed, "use witnessd
  to do <goal> with 2 lanes" yields a verified parallel run without the
  human touching the CLI.
  **Naming principle (appended 2026-07-04): skill names are task-verbs, not
  engine brands.** Repos keep their brand names (witnessd/Depone — renaming
  public repos and contract kind strings is churn for no user value), but
  what a session sees must say the job: the runner skill ships as
  **`proofrun`** ("provable run" in one word; triggers include verified run
  / proven / 증명 실행), the audit skill as **`verify-evidence`**, with
  "powered by witnessd × Depone" as the engine credit. Skill discovery is
  name+description matching, so intuitive naming is invocation accuracy,
  not cosmetics.
- Depone CI PAT for reverse conformance (D4).
**Bar:** on a clean macOS *and* Linux machine, `git clone && witnessd init
&& witnessd run` (shell adapter) to green verdict in under 10 minutes,
scripted as `scripts/quickstart_check.sh` and run in CI.

### W19 — Live parallel proof + dogfood default  *(operator, paid)*
The first **live** multi-agent parallel run: ≥2 real codex lanes on a real
external repository, concurrently, full evidence, Depone re-derivation,
committed as the flagship fixture (like the W10/pilot lineage). From this
wave on, the operator's own real tasks run through `witnessd run` by
default — every real use accrues fixture-grade mileage. **Bar:** flagship
fixture committed + `revalidate_w19.py`; a "mileage" README section that
counts real runs honestly.

### W20 — AAL-4: keyless identity + transparency log  *(the thesis completed)*
Implements D1/D2/D3. witnessd `--anchor`: after a bundle is sealed, the
anchor process obtains a Fulcio keyless cert (operator OIDC identity),
countersigns the bundle digest, submits to Rekor, and stores cert + Rekor
inclusion proof + checkpoint **into the evidence directory as bytes**.
Depone (contract PR first, additive): `validate_keyless_anchor` — verifies
the certificate chain to Fulcio roots, the signature, and the Rekor Merkle
inclusion proof against the pinned checkpoint, **entirely offline from
persisted bytes**; verdicts gain `trust_root: operator-key |
keyless-anchored`. The W6a linter's fixture-only boundary is retired in
favor of real verification; `ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED`
disappears. D3 lands here too. **Bar:** an anchored run whose evidence a
fresh machine verifies offline to `keyless-anchored` **without any operator
public key configured**; negative fixtures (wrong cert chain, forged
inclusion proof, checkpoint mismatch) all rejected; `revalidate_w20.py`.
*This is the wave after which a stranger can trust a witnessd run.*

### W21 — Declarative verification policy layer
Depone gains stdlib-evaluated JSON policies ("this repo requires: A2 lanes
only, max_overlap ≥ 2, keyless-anchored, regions within src/") replacing
hardcoded revalidator logic for *requirements* (validators stay for
*integrity*). witnessd `run` can carry a policy reference; the verdict
reports policy compliance. Witness/OPA is the consensus shape; ours stays
stdlib. **Bar:** the W15/W19/W20 fixtures re-verified through policies;
a policy that today's evidence *fails* produces an honest fail;
`revalidate_w21.py`.

### W22 — The standard: publish the contract
- Evidence contract published as a versioned spec document in Depone
  (schemas, error codes, canonical hash, assurance/trust axes) with
  conformance fixtures as the test kit.
- OVERT interop: a mapping document + exporter producing OVERT-compatible
  governance views from witnessd evidence (interop, not adoption).
- OTel GenAI: schedule/lane spans exported via the existing `otel_spans`
  path, aligned to the GenAI semantic conventions.
- witnessd goes public (D4). gh releases for both repos; the README leads
  with the thesis and the live W19/W20 fixtures as proof.
**Bar:** an external implementer could build a compatible emitter from the
published spec alone (checked by a "spec-only" conformance test that uses
no witnessd code); OVERT/OTel exports validate against their schemas.

### Post-1.0 (recorded, not planned): distributed multi-host lanes,
container-grade isolation, replay-determinism hardening, third-party
adapter SDK, independent notary federation (beyond single Rekor).

## 4. Release map

| Version | Waves | Meaning |
|---|---|---|
| v2.2.0 | W15+W16 | true parallel runtime, merge-complete |
| v2.3.0 | W17+W18 | durable + installable (a real tool) |
| **v3.0.0** | W19+W20 | **thesis complete: live parallel + stranger-verifiable (AAL-4)** |
| v3.1.0 | W21+W22 | policy + published standard, repos public |

## 5. Adoption track (parallel to all waves; not code)

Runs alongside, never blocks, never blocked: (1) every real operator task
goes through witnessd from W19 on; (2) external user #1 is recruited after
W18 (installable) and their run becomes fixture-grade evidence after W20
(they need not trust us — that is the point); (3) feedback lands as issues
tagged to waves, not as re-litigation of this spec.

---

*Maintenance rule: this file changes only by append (new decisions, wave
outcome notes) or by an explicit versioned revision commit. It is the answer
to "what's next" — the answer is always: the next unfinished wave above.*
