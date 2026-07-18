# CLI Monolith Split (P0.7) — Design

> Approved 2026-07-18. Staged over three PRs; each PR gets its own implementation plan authored against fresh line anchors after the previous PR lands. Recon baseline: main @ 1da6907.

**Goal:** Split `witnessd/__main__.py` (5,480 lines: 47 `_cmd_*` handlers + ~55 helpers interleaved + a 583-line `_build_parser`) into a `witnessd/cli/` package, converging on the architecture Depone's CLI already has (pure argparse shell + lazy-dispatched submodules). Behavior stays byte-identical.

**Depone verdict:** `depone/__main__.py` (968 lines) needs **no split** — it is already argparse-wiring-only with all logic in `depone.cli.*` via `_LAZY_MODULES`/`_load(x).run(args)`. It is the reference model, not a candidate.

## Target architecture

- New package `witnessd/cli/`; handler clusters move to `witnessd/cli/<cluster>.py` **verbatim** (the diff for each moved function must be a pure move — import lines only may change).
- `witnessd/__main__.py` ends as: parser wiring + argv normalizers (`_normalize_*`) + `ORRO_COMMAND_MAP`/`ORRO_COMMANDS` + lazy dispatch + `main()` (~830 lines). The 585-line `_build_parser` stays intact: it is pure argparse wiring with no direct handler references beyond `_cli_handler` closures, so decomposing it adds no useful boundary.
- Dispatch: `set_defaults(func=_cli_handler("<module>", "<name>"))` where `_cli_handler` returns a closure that `importlib.import_module`s the cli module **at call time** — parser build stays import-free, preserving today's lazy-import economics (105 in-function imports move verbatim with their functions).
- Shared helpers hoist FIRST into `witnessd/cli/_output.py`: `_emit_orro_error` (60 call sites), `_structured_error`, `_write_json_file`, `_json_or_text`, `_hash_file`, `_read_runlog`, `_depone_subprocess_env`, `_run_depone_json`. Remaining `__main__` callers use a module-scope `from witnessd.cli._output import ...` (stdlib-only at module scope, so eager import is cheap); extracted cli modules import from `witnessd.cli._output` directly.

## Staging

| PR | Scope |
|---|---|
| 1 | `cli/_output.py` (shared 8) + least-entangled clusters: advisory/continuation (next/advise/sketch/trace/report/review/auto — already ~90% delegated to `orro_*` modules), pilot, lifecycle/faultkit (doctor/isolation/faultkit/pause/resume/kill/learn/install/status/verify), bootstrap (init/orro-setup/scout/route) |
| 2 | orro-flow orchestration (~565 lines, helpers flow-local) → `cli/flow.py`; team-go (~600) → `cli/team_go.py`; team plan-run/resume/a2/ledger/lane-exec → `cli/team_ops.py` |
| 3 | The shared-helper nexus: run/run-goal/proofrun, proofcheck/handoff/doctor-orro/engine-lock, plan/flowplan; no separate `cli/team_specs.py` — `_default_team_lane_command` and `_lane_packet_to_run_team_spec` already moved to `cli/team_ops.py` in PR2, while sole-caller helpers `_role_lane_plan_team_specs` and `_attach_role_capability_team_fields` fold into `cli/run.py`; `_build_parser` remains intact; final `__main__` slim-down to ~830 lines |

## Invariants (every PR)

1. **Byte-identical behavior**: same commands, flags, stdout/stderr shapes, exit codes. No command or module renames — `python3 -m witnessd <cmd>` strings are baked into emitted evidence/guidance, and `-m depone` spawns are live.
2. **`witnessd.__main__` keeps exporting**: `main`, `ORRO_COMMANDS`, `ORRO_COMMAND_MAP`, `_normalize_orro_argv` (pinned by `orro/__main__.py` console-script shim and tests).
3. **No re-export shims for moved internals.** Tests that import or monkeypatch moved names (`_role_lane_plan_team_specs`, `_parse_team_lane`, `_parse_team_merge_group`, `_codex_specs_are_isolated`; patch targets `witnessd.__main__._run_depone_json` / `.Path` / `.subprocess`) are updated to the new home **in the same PR** — patch targets follow the *usage site's* namespace.
4. **Moves are verbatim**: function bodies unchanged; only import statements may be added/removed. Reviewer enforces "move-only" by diff inspection.
5. Each PR is independently CI-green and mergeable; the suite runs between every extraction task.
6. `_cmd_self_test` semantics unchanged (it imports sibling modules directly, not via CLI dispatch).

## Verification (per PR)

- Clean-env full suite (baseline 821 OK) + `self-test --all`.
- **Behavior-diff smoke**: capture, on the pre-PR commit, the byte output of (a) `--help` for every subcommand (top-level and `orro <cmd> --help`), (b) one structured-error path, (c) one benign end-to-end command; re-capture on the PR head and diff — must be byte-identical (modulo nothing).
- Live smoke of representative moved commands through the real CLI.
- Mutation testing is not applicable to pure moves; the move-only diff review substitutes for it.

## Risks

- Monkeypatch-target drift (tests patching `witnessd.__main__.X` silently patching the wrong namespace after a move → test passes for the wrong reason). Mitigation: every moved handler's tests are grepped for `patch(` targets in the same task that moves it.
- Hidden cross-cluster helper use discovered mid-move. Mitigation: the recon's shared-helper table is the checklist; any newly discovered shared helper goes to `_output.py` (or stays until PR3), never duplicated.
- Import-cycle regressions from eager imports. Mitigation: `cli/` modules keep in-function imports verbatim; only `witnessd.cli._output` may be imported eagerly by `__main__`.
