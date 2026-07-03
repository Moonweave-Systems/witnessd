# W16 — Merge lanes

## Problem

SPEC3 requires witnessd to bridge overlapping lane regions to Depone's existing
merge evidence contract: overlapping source lanes must be followed by an
explicit merge lane, the merge lane must be sequenced after its sources in the
W15 schedule receipt, and unresolvable conflicts must be recorded as blocked
evidence with `ERR_TEAM_MERGE_CONFLICT_UNRESOLVED` instead of hidden retries
(`SPEC3.md:139-150`).

GOALMODE makes the same order constraint operational: read Depone's existing
`validate_team_merge_attempt_receipt` first, do not invent a parallel schema,
record the merge lane after source lanes, keep overlap proof unpolluted by the
merge lane, and attach conflict bytes for blocked conflicts
(`docs/plans/GOALMODE.md:139-144`).

Measured current state:

- Depone already exposes `build_team_ledger_merge_receipt` for the older
  deterministic ledger merge receipt shape (`team_ledger.py:183-210`).
- Depone derives overlap from passed lane bytes via
  `_find_overlapping_touched_files`, using lane `worktree_receipt.changed_files`
  or `touched_files`, plus lane `end_commit` when present
  (`team_ledger.py:1662-1695`).
- Depone requires a relative `merge_receipt` file when overlapping passed lanes
  exist and validates that file under the ledger base directory
  (`team_ledger.py:1698-1778`).
- Depone also accepts the stronger `depone-team-merge-attempt` receipt and
  checks producer validity, `decision=pass`, `exit_code=0`,
  `dirty_target_refused=false`, disposable cleanup, no `conflict_files`, and
  coverage of every overlapping file and lane `end_commit`
  (`team_ledger.py:2181-2254`).
- witnessd already has a simple local builder for
  `team-ledger-merge-receipt`, and its ledger builder already links
  `merge_receipt` and `schedule_receipt`.
- witnessd planner currently calls `_assert_region_disjoint` from
  `seal_plan`, and `_assert_region_disjoint` rejects repeated normalized
  region paths with `ERR_PLAN_REGION_OVERLAP` (`witnessd/planner.py:143-145`,
  `witnessd/planner.py:242-248`).

## Contract delta

No Depone schema change is allowed or needed for W16. The witnessd side must
emit bytes that satisfy Depone's existing contract:

- Ledger field: `merge_receipt` is a repo-relative path under the ledger base
  directory.
- Preferred receipt kind for W16: `depone-team-merge-attempt`, produced by
  Depone's existing `team_merge_attempt` helper and validated through
  `validate_team_merge_attempt_receipt`.
- Passing merge receipt requirements: `schema_version=0.1`,
  `decision=pass`, `exit_code=0`, `dirty_target_refused=false`,
  `cleanup.attempt_worktree_removed=true`, empty `conflict_files`,
  `merged_files` covering every overlapping path, and `head_commits` covering
  every overlapping source lane `end_commit`.
- Blocked conflict evidence is not linked as a passing ledger merge receipt.
  witnessd records the blocked merge lane and conflict artifacts with
  `ERR_TEAM_MERGE_CONFLICT_UNRESOLVED`; Depone continues to reject the team
  ledger as not pass until a valid passing merge receipt exists.

Additive proof: witnessd only writes the already-supported ledger
`merge_receipt` pointer and receipt file. Depone code and schemas remain
unchanged.

## Design

Planner:

- Keep `_assert_region_disjoint` as the guard for implicit overlap. Disjoint
  plans keep the exact W15 behavior.
- Introduce an explicit merge-group representation in sealed plans. A merge
  group names source lane ids, overlapping paths, and a merge lane packet.
  Source lane overlap is accepted only when every overlapping path is covered
  by exactly one merge group and the merge lane is not counted as a source lane.
- Reject any overlap not covered by a merge group with
  `ERR_PLAN_REGION_OVERLAP`; reject malformed merge groups with explicit
  planner errors. A stray `merge_lane` flag must not bypass the region guard.

Scheduling and fan-in:

- Source lanes run under the W15 supervisor exactly as before.
- Merge lanes become a second scheduling wave after their source lanes have
  exited and been reaped. The W15 schedule receipt records each source lane
  interval and then the merge lane interval; acceptance checks verify source
  lanes overlap and the merge lane starts after every source exit.
- The overlap proof is derived from source lane intervals and source lane
  touched bytes only. The merge lane's own interval and touched files are
  excluded from the concurrency proof so it cannot inflate `max_overlap`.

Merge execution:

- The merge lane reconciles source lane worktree outputs by invoking the
  existing Depone merge-attempt producer against the canonical repo base and
  the source lane `end_commit` values. It runs quota-free, uses only local git,
  and launches no agents or live models.
- On pass, witnessd writes the merge-attempt receipt under the run/ledger
  evidence directory, links it from `build_team_ledger(..., merge_receipt=...)`,
  and signs/bundles through the existing `build_bundle` path.
- On conflict, witnessd captures conflict file paths and conflict bytes under
  the merge lane evidence directory, marks the merge lane blocked with
  `ERR_TEAM_MERGE_CONFLICT_UNRESOLVED`, and does not silently retry or emit a
  fake passing merge receipt.

Honest boundaries:

- witnessd orchestrates processes and records evidence; Depone validates bytes.
- Depone executes no agents and does not approve merges.
- No W17 replay-resume behavior is introduced.
- Tests and scripts use `sys.executable` for Python subprocesses and fake/shell
  adapters only; no `codex` or `uv` assumption.

## Tasks

1. RED: planner tests for explicit merge groups.
   - Files: `tests/test_planner.py`, `witnessd/planner.py`.
   - Add tests proving disjoint plans are unchanged, implicit overlap still
     raises `ERR_PLAN_REGION_OVERLAP`, covered source overlap seals with a
     merge lane scheduled after its sources, and a malformed/unrelated merge
     lane does not bypass the guard.

2. GREEN: planner merge-group normalization.
   - Files: `witnessd/planner.py`.
   - Add validated merge-group metadata to sealed plans without changing lane
     packet hashes for ordinary disjoint plans.

3. RED: fan-in merge lane receipt tests.
   - Files: existing fan-in/team tests plus focused new tests if needed.
   - Build a tiny git repo where two source lane commits touch the same file,
     then assert fan-in writes a Depone-valid merge-attempt receipt, links it
     from the ledger, and records the merge lane after sources.

4. GREEN: fan-in merge execution.
   - Files: fan-in/orchestrator modules and `witnessd/team_ledger.py` only as
     needed.
   - Reuse Depone's `build_team_merge_attempt_receipt`; do not add a parallel
     schema. Preserve W15 nursery and fail-fast semantics.

5. RED/GREEN: conflict evidence.
   - Add a fixture/test where the source commits cannot merge. Assert blocked
     `ERR_TEAM_MERGE_CONFLICT_UNRESOLVED`, attached conflict bytes, all
     children reaped, and no passing `merge_receipt` is linked.

6. RED/GREEN: W16 revalidator and fixtures.
   - Files: `scripts/revalidate_w16.py`, `fixtures/w16/`.
   - Fixture: two source lanes touching one shared file plus merge lane;
     Depone re-derives pass-with-merge.
   - Negative fixture: forged/tampered merge receipt rejected by Depone.

7. Regression floor and outcome note.
   - Run GOALMODE §2 floor, W16 acceptance, and §5 adversarial checks.
   - Append the dated outcome note to this plan doc before the final local
     implementation commit.

## Negative fixtures

- Missing merge receipt for overlapping passed lanes must produce
  `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED`.
- Forged merge receipt that omits a required overlapping file or source lane
  commit must be rejected with coverage errors.
- Forged `depone-team-merge-attempt` receipt with `decision=pass` but
  `exit_code != 0`, non-empty `conflict_files`, dirty target, or missing
  cleanup must be rejected.
- A planner merge lane that is unrelated to the overlapped source paths must
  not bypass `ERR_PLAN_REGION_OVERLAP`.
- Conflict fixture must remain blocked and preserve conflict bytes; no silent
  retry and no fabricated passing receipt.

## Acceptance bar

Run from the sibling checkout root:

```bash
cd depone
python3 -m unittest discover -s tests

cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for s in scripts/revalidate_*.py; do PYTHONPATH=../depone python3 "$s" || exit 1; done
git diff --check
tmp=$(mktemp -d)
git archive HEAD | tar -x -C "$tmp"
cd "$tmp" && PYTHONPATH="$(cd ../depone && pwd)" python3 scripts/revalidate_key_rotation.py
```

Additional W16 bar:

- `PYTHONPATH=../depone python3 scripts/revalidate_w16.py` passes.
- The W16 fixture contains two source lanes touching one shared file and a
  merge lane whose schedule interval starts after both source exits.
- Depone re-derives pass-with-merge from the fixture bytes.
- Depone rejects the committed forged merge receipt fixture.
- Quota audit: tests use shell/fake adapters only; zero paid calls.
- `ingest_signed_evidence_bundle` diff remains zero.

## Out of scope

- No Depone contract/schema changes.
- No W17 replay-resume, attempt-history journal, or distributed execution.
- No live Codex lanes, paid runs, OIDC, policy gates, production gate edits,
  archive edits, or operator review edits.
- No hidden conflict resolution, silent retry loop, or merge approval claim.
