# GOALMODE — standing orders for autonomous execution to the endgame

**Audience:** an autonomous coding agent (Codex goal mode) executing
`SPEC3.md` to completion without per-wave human direction.
**Authority order:** SPEC3.md (what & why, decided) → this file (how to
execute autonomously) → per-wave plan docs in `docs/plans/` (detail).
If they ever conflict, stop and flag; do not improvise around a conflict.

---

## 1. The loop (run this until SPEC3 §3 has no unfinished wave)

```
1. next_wave := first unfinished wave in SPEC3 §3 (W15 → W16 → … → W22)
2. if docs/plans/ has no detailed plan for next_wave:
       WRITE IT FIRST (template in §6), commit it, then implement from it.
       The plan is part of the wave — never code straight from SPEC3 prose.
3. implement the plan task-by-task, strict TDD (RED → GREEN per task).
4. run the REGRESSION FLOOR (§2). all green or the wave is not done.
5. run the wave's own acceptance bar (SPEC3 §3) — including negative tests.
6. self-adversarial pass (§5). fix what it finds; re-run floor.
7. commit locally (one logical commit per task or coherent group;
   messages explain WHY). DO NOT PUSH (§4).
8. write the wave outcome note: append a dated entry to the wave's plan doc
   (what landed, what deviated, evidence of the bar being met).
9. if an OPERATOR CHECKPOINT (§4) blocks the next step: stop, emit the
   checkpoint report, and wait. otherwise goto 1.
```

## 2. Regression floor (exact commands; every wave, both repos)

From `~/moonweave-check` (or the workspace root in use); Depone path may be
derived from the sibling checkout — never hardcode absolute machine paths.

```bash
# Depone
cd depone
python3 -m unittest discover -s tests                # all green
# witnessd (uv wrapper only where the repo hook demands it; CI uses plain python3)
cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for s in scripts/revalidate_*.py; do PYTHONPATH=../depone python3 "$s" || exit 1; done
git diff --check
# portability floor (W14 standard): export-root re-derivation
tmp=$(mktemp -d); git archive HEAD | tar -x -C "$tmp"
cd "$tmp" && PYTHONPATH=<abs-depone> python3 scripts/revalidate_key_rotation.py
```

Floor invariants that must never regress, wave after wave:
- `production_gate.status == "open"`, 5/5 recorded (never edit the archive).
- `depone/agent_fabric/evidence_substrate.py::ingest_signed_evidence_bundle`
  diff = 0 across the entire endgame.
- Depone executes nothing, ever. witnessd core = stdlib + openssl (+ `ps`
  read-only; + `sigstore` CLI inside `witnessd/anchor/` only, from W20).
- Both platforms first-class: path comparisons via `realpath`; in-fixture
  evidence references relative; subprocess via `sys.executable` (never a
  hardcoded `uv`); no `/home/ubuntu` or `/Users/...` literals in code/tests.
- **CI has NO `codex` binary and NO `uv` on PATH** (only plain `python3`).
  Any test exercising an adapter lane must pass an explicit fake binary
  (`codex_binary=...`), never rely on a real `codex`/PATH lookup; any
  subprocess uses `sys.executable`. A green local run on this VM (which HAS
  codex+uv) is NOT proof — the regression floor is only met when it would
  pass in a codex-absent, uv-absent, plain-`python3` environment. (Learned
  the hard way at W15: `budget_exceeded` vs `preflight_blocked` diverged by
  codex presence.)
- evidence-pending output rule; worker never self-seals; assurance cap A2
  (trust axes rise in W20, assurance cap does not).
- Timestamps real; anything unavailable is recorded `unavailable` — the
  agent NEVER fabricates evidence values, test outputs, or verdicts. If a
  bar cannot be met honestly, stop and report; a red honest state beats a
  green dishonest one (fail-closed is the product).

## 3. Cross-repo protocol (order is not optional)

Any wave touching the evidence contract (W16 merge bridge fixtures? no —
contract already exists; W17 resume_receipt: YES; W20 keyless anchor: YES;
W21 policy: YES, additive; W15 schedule receipt: YES):
1. Depone change first, in the Depone repo, additive-only, its own commits,
   full Depone suite + conformance fixtures green.
2. STOP at the push checkpoint (operator pushes Depone; witnessd CI clones
   Depone main — pushing witnessd first breaks CI; this is a proven failure
   mode, not a theory).
3. Only then the witnessd half.
Never make witnessd depend on an unpushed local Depone change beyond the
current in-flight wave, and say so in the checkpoint report when it does.

## 4. Operator checkpoints (the agent MUST stop; these are product security
   design, not missing autonomy)

| Checkpoint | Why the agent cannot do it |
|---|---|
| `git push` (both repos, every time) | push authority is operator-only (D7); guard-enforced |
| `production_gate` / archive / operator_review edits | the product exists to prevent exactly this |
| Paid runs (W19 live codex lanes; any `--codex-auth` execution) | operator quota + judgment |
| OIDC identity for Fulcio (W20 anchor login) | a human identity attests, not the agent |
| Clean-machine quickstart validation (W18 bar) | needs a machine the agent doesn't own |
| Repo publication + releases (W22, D4 PAT at W18) | irreversible, outward-facing |

Checkpoint report format: what is done, exact commands the operator must
run, what unblocks, which wave resumes after. Then wait.

## 5. Self-adversarial pass (before declaring any wave done)

Attack your own wave the way the independent reviewer will:
- Try to FORGE the wave's new evidence (edit a byte, self-declare a field,
  reuse another lane's receipt) — Depone must reject each attempt; these
  become committed negative fixtures, not throwaway checks.
- Tautology hunt: does any validator read the value it is checking from the
  same place the emitter wrote it, without an independent derivation?
  (max_overlap must come from intervals; resume-skip must come from
  re-derivation; policy pass must come from evidence, not runtime say-so.)
- Concurrency/kill honesty: kill things mid-run in tests; orphan scan
  (no child processes may outlive the orchestrator in any exit path).
- Platform: would this pass on the other OS? (symlinked tmpdirs, /proc
  absence, second-resolution starttime).
- Quota audit: zero paid calls occurred (until the W19 checkpoint).

## 6. Wave-plan template (write before implementing; commit as
   `docs/plans/<date>-w<N>-<slug>.md`)

```
# W<N> — <name>
Problem (from SPEC3 §3, plus measured current state — cite files/lines)
Contract delta (exact Depone schemas/validators/error codes; additive proof)
Design (components, data flow, failure semantics, honest boundaries)
Tasks (TDD: RED test → GREEN impl, per task; exact files)
Negative fixtures (the forgeries this wave must reject)
Acceptance bar (SPEC3 bar restated as runnable commands)
Out of scope (what this wave deliberately does not do)
```

## 7. Per-wave execution notes (traps known today; the plan doc elaborates)

- **W15** (plan exists: `2026-07-03-w15-parallel-execution-core.md`): traps —
  runlog single-writer; nursery guarantee on *exception* paths; monotonic/
  wall-clock pair consistency; schedule receipt written only after all
  children reaped.
- **W16 merge lanes:** Depone `validate_team_merge_attempt_receipt` already
  exists — read it FIRST and build the witnessd bridge to the existing
  contract; do not invent a parallel merge schema. Merge lane is sequenced
  AFTER source lanes in the schedule receipt (overlap proof must not be
  polluted by the merge lane). Conflict = evidence (`blocked:
  ERR_TEAM_MERGE_CONFLICT_UNRESOLVED` + conflict bytes), never a silent
  retry.
- **W17 replay-resume:** resume decisions come from RE-DERIVATION (run the
  validators over surviving bytes), never from the journal's own claim of
  completion. Attempt history is append-only; prior partials retained.
  Contract: `resume_receipt` additive → Depone first.
- **W18 DX:** installer must pin Depone by tag + record both repo hashes;
  quickstart script becomes CI (`scripts/quickstart_check.sh`); no curl-pipe
  installs; PAT setup is operator checkpoint.
- **W19 live parallel:** everything up to the paid run is agent work
  (tooling so the run is ONE command); the run itself is operator.
  Fixture-ize the result exactly like the W10/pilot lineage.
- **W20 AAL-4:** anchor = separate process in `witnessd/anchor/`, invoked
  AFTER bundle sealing; sigstore as external CLI only (D2); absent CLI ⇒
  `ERR_WITNESSD_ANCHOR_UNAVAILABLE`, run stays valid operator-key. Depone
  verifies cert chain + Rekor Merkle inclusion OFFLINE from bytes against a
  pinned checkpoint — no network in Depone, ever. D3 (process_identity_source)
  lands here. OIDC login = operator checkpoint.
- **W21 policy:** policies constrain REQUIREMENTS (what must be true);
  validators keep owning INTEGRITY (whether bytes are honest). A policy can
  never upgrade a verdict, only demand more.
- **W22 standard:** the spec-only conformance test must not import witnessd
  code (an external implementer's view); OVERT/OTel exports validate against
  their published schemas; publication itself = operator checkpoint.

---

*This file is append-only, like SPEC3. When a wave completes, its outcome
note lives in the wave plan doc; when a standing order changes, append the
change here with a date and reason.*
