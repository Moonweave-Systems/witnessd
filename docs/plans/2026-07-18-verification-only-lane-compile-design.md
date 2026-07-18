# Verification-Only Profile → Real Lane Compilation (Design)

> Design spec (approved via brainstorm 2026-07-18). Implementation plan follows separately.

**Goal:** Make the `verification-only` workflow profile compile into a real, executed witnessd lane that runs declared, deterministic verification checks under observation with **zero granted write scope**, producing fresh evidence that Depone independently verdicts.

**Status quo being fixed:** the `verification-only` profile is inert on the lane plane — its roles are `verifier` (engine=Depone, proofcheck, `may_verify=True`) and `handoff` only, so `compile_role_lane_plan` emits **zero lanes** (`execution_allowed = profile in {"code-change", "docs-change"}`, `witnessd/orro_workflow.py:249`). The only working "verification-only" today is the flag path (`orro flow --verification-only` → `code-change` profile + `lane_intent` stamp, `witnessd/__main__.py:3382-3404`), which grants a write scope and relies on post-hoc falsification (a *promise*). This design adds the *structural* variant: a lane that cannot mutate because it is never granted a write region.

## Product semantics (decided)

- The compiled lane **executes declared non-mutating checks** (e.g. `pytest`, `ruff check`, `mypy`) under observation. It does not re-verify existing evidence bytes (that remains Depone's `proofcheck` role, unchanged).
- **Deterministic only (adapter=shell).** No LLM sits inside a verification lane: putting an AI agent in charge of "which checks ran" would reintroduce the self-report problem this product exists to eliminate. AI adapters are rejected fail-closed for this profile.
- **Invariant preserved:** witnessd *executes* the checks; Depone alone *verdicts* the sealed evidence. The check-runner lane carries `may_verify=False`, `raises_assurance=False`.

## Scope boundary

**witnessd-only change. No Depone contract change.** Depone v0.2.2 already accepts the exact evidence shape this lane produces: ledger lane with `lane_intent="verification-only"` + empty `touched_files`/`changed_files` → `pass` (`depone/agent_fabric/team_ledger.py:529-556`); any mutation → `ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED` (`:541-556`). Both gates stay as-is and this lane must satisfy them.

## Architecture (6 changes)

| # | Change | Anchor (main @ eaa4f4e) |
|---|--------|-------------------------|
| 1 | Add a `check-runner` role to the `verification-only` profile: engine=`witnessd`, phase=`proofrun`, `may_execute=True`, `lane_intent="verification-only"`. Flow gains `proofrun` before `proofcheck`; `engine_calls` gains a proofrun call; `required_gates` gains check-execution gates. `_role` already accepts `lane_intent` (`orro_workflow.py:1484-1503`). | `orro_workflow.py:1366-1391` |
| 2 | Extend `compile_role_lane_plan` so the `verification-only` profile compiles its proofrun role via a new `_verify_lane_from_role` factory (beside `_role_lane_from_role`/`_review_lane_from_role`/`_critic_lane_from_role`). The Depone `verifier` role (`may_verify=True`, no `may_execute`) is naturally excluded by the existing proofrun+`may_execute` filter. Lane fields: `adapter="shell"` (forced), `region=[]`, `lane_intent="verification-only"`, `may_execute=True`, `may_verify=False`, `raises_assurance=False`, plus `check_commands=[...]`. `summarize_executable_lanes` and `_validate_role_lane` admit the new shape. | `orro_workflow.py:249-278, 296-327, 1111-1148` |
| 3 | Exempt the verification lane from the write-scope-required check (`ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED` stays for `code-change`; empty region is the *point* here). No auto-synthesized region (unlike docs-change). | `orro_workflow.py:935-941` |
| 4 | Supply checks explicitly: `flowplan --profile verification-only --role-lanes-out ... --check "<cmd>"` (repeatable). **≥1 required when compiling role lanes** for this profile (fail closed); `--check` with any other profile, or without `--role-lanes-out`, → error. Plain workflow-plan-only flowplan (no `--role-lanes-out`) stays valid without checks. Threads `compile_role_lane_plan(check_commands=...)` → lane `check_commands` → team spec `commands` (each check runs as `["sh", "-c", check]`). In the spec builder's shell branch, declared checks replace `_default_team_lane_command` (which *writes* into the region — never used for this lane). The workflow-plan schema itself gains no field. | `__main__.py:5311` (flowplan args), `:625-687` (spec builder), `:4557-4566` (default cmd, bypassed) |
| 5 | **Core new mechanism:** `run_team` currently audit-and-skips any lane with empty `allowed_touched_files` (`read-only-lane-audit` + `continue`). For lanes whose spec **declares** `lane_intent="verification-only"`, execute instead: create the lane worktree, run the declared checks, seal receipts. Keyed on the declaration, never derived from observed emptiness (anti-circularity rule from the #70 wave). All other claimless lanes keep audit-and-skip byte-identically. | `fanin.py:136-143` |
| 6 | Observable work + failure on the shell path need **no new gate** — both are already discharged: (a) every declared check produces a command receipt (`adapters/shell.py:109-115`, even OS errors record exit 127), and check_commands are validated non-empty, so zero-receipt lanes are impossible by construction; (b) any receipt with non-zero exit already flips the lane to `blocked` + `ERR_TEAM_LANE_FAILED` (`fanin.py:1720-1726`). `lane_intent` emission into ledger lanes already exists for all builders (#124). | `fanin.py:1720-1726` (existing, unchanged) |

## Data flow

```
flowplan --profile verification-only --check "pytest -q" --check "ruff check ."
  → workflow plan (check-runner role, lane_intent=verification-only)
  → role-lane plan (1 shell lane, region=[], check_commands=[...])
proofrun / team run
  → run_team: claimless lane executes (declared intent) in its worktree
  → declared checks run; command_receipts record cmd + exit codes; no writes
  → _commit_lane early-returns (nothing staged) → changed_files=[] (worktree.py:122-124)
  → team-ledger.json lane: lane_intent=verification-only, touched_files=[]
proofcheck (Depone, unchanged)
  → empty + declared → pass
  → mutated → ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED → blocked
```

## Error handling / falsifiability

- Declared check exits non-zero → lane goes `blocked` + `blocked_reason=ERR_TEAM_LANE_FAILED` via the **existing** `_run_write_lane` failure path (`fanin.py:1720-1726`). The Depone contract has only `{"pass", "blocked"}` states (`team_ledger.py:51`) — no new state is invented. Verification honestly failing is a first-class outcome; evidence is still sealed and visible in receipts.
- No checks declared when compiling role lanes → flowplan fails closed (structured error, no lane compiled). `--check` outside `verification-only`+`--role-lanes-out` → fail closed.
- Checks mutate the worktree → `changed_files` non-empty → Depone falsification gate blocks (existing, untouched).
- AI adapter requested for this profile → fail closed at compile. `--lane-intent implementation` combined with the `verification-only` profile → fail closed (contradictory declaration).
- The lane's `prompt` is a real deterministic description of the declared checks (not the placeholder prefix), so `assert_role_lane_prompts_explicit` needs no exemption.

Both falsifiability gates (witnessd zero-observable-work; Depone mutation) remain enforced; neither is weakened for any other lane kind.

## Documented invariant flip (deliberate, spec-level)

This design **reverses a documented product invariant**: "review-only, verification-only, and default release-readiness role-lane plans cannot launch proofrun." After this change, `verification-only` role-lane plans DO launch proofrun (as claimless deterministic check lanes); `review-only` and default `release-readiness` stay blocked. Every statement of the old invariant is revised in the same change:

- `SPEC3.md:253-254` (SoT), `CLAUDE.md:160`, `SKILL.md:187`, `docs/README.md:120`
- Pinned tests updated to the new contract: `tests/test_orro_workflow.py:138-154` (profile has no executes call), `:156-172` (phase gate rejects verification-only proofrun), `:410-429` (profile compiles zero lanes), `tests/test_orro_public_flow.py:1588+` (forbidden-profiles loop includes verification-only)

## Out of scope (v1, deliberate)

- `orro flow --verification-only` flag semantics stay as-is (code-change + intent stamp; pinned by tests; changing it is a separate product decision).
- Config-file/persisted check declarations (CLI-only for v1).
- AI-adapter verification lanes.
- Any Depone change or schema bump; review-only/critic lane paths untouched.
- ORRO repin (happens with the next witnessd release, normal ordering).

## Testing & verification

- Unit: profile compiles exactly one shell verification lane with threaded `check_commands`; missing `--check` and wrong-profile `--check` fail closed; write-scope exemption applies only to this profile; spec builder never emits `_default_team_lane_command` for this lane; `run_team` executes the declared claimless lane while other claimless lanes still audit-and-skip (regression pin).
- E2E: flowplan → run → ledger → Depone `pass` with a benign check; a deliberately mutating check → `ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`; a failing check → lane failure recorded.
- Independent verification (post-implementation): clean-env full suite (`PATH` without shims, `PYTHONNOUSERSITE=1`, pinned Depone via `PYTHONPATH`/`WITNESSD_DEPONE_ROOT`; baseline 806 OK); mutation testing (disable the run_team execution branch, the `--check` threading, and the receipts gate — each must break a test); hermetic live smoke with the real CLI (`--check "/usr/bin/python3 -c pass"` — no vendor CLI or auth needed).
