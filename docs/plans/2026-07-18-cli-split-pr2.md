# CLI Split PR2 — Orchestrator Clusters (flow, team-go, team-ops)

> Design authority: `docs/plans/2026-07-18-cli-monolith-split-design.md`. Hard rules of PR1's plan apply verbatim (move-only; the call-site lazy-import amendment; no re-exports; no renames). Line anchors: main @ 2fe7679 (`__main__.py` = 4,410 lines). The moving region is contiguous: **1901-3532**.

**Test command:** `RUN='env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest'` — baseline 821 OK, 17 skipped.

**Extraction order matters** (new-module dependency graph is acyclic: flow→{team_go,team_ops}, team_ops→team_go): Task 1 team_go → Task 2 team_ops → Task 3 flow. Cross-module names between NEW cli modules use plain module-scope imports (they load lazily at dispatch anyway); only callers that STAY in `__main__` use the amendment's in-function import.

### Task 1: team-go cluster → `witnessd/cli/team_go.py`

- [ ] Move verbatim: `_fill_interactive_team_init_args` (2584), `_cmd_team_go` (2593), `_team_go_reference_adapter_lanes` (2952), `_team_go_reference_adapter_warning` (2988), `_team_go_routing_decision` (3012), `_invoke_cli_capture` (3046), `_load_json_if_exists` (3059), `_write_team_go_report` (3066), `_emit_team_go_result` (3084), `_apply_lane_prompt_files` (3148).
- [ ] `team_go.py` module scope: stdlib `argparse, io, json, os, shlex, sys`, `from contextlib import redirect_stderr, redirect_stdout`, `from pathlib import Path`; plus `from witnessd.cli._output import _json_or_text, _structured_error, _write_json_file` (only names actually referenced — grep the bodies).
- [ ] Rewire parser: `team_go.set_defaults(func=_cmd_team_go)` (4003) → `_cli_handler("team_go", "_cmd_team_go")`.
- [ ] Amendment call-site imports in STAYING code (in-function, immediately before first use):
  - `_cmd_run_goal` :362 → `from witnessd.cli.team_go import _team_go_reference_adapter_lanes`
  - `_cmd_run_goal` :445 → `from witnessd.cli.team_go import _team_go_reference_adapter_warning`
  - `_cmd_proofcheck` :1331 → `from witnessd.cli.team_go import _load_json_if_exists`
  (Line 443's `_proofrun_reference_adapter_warning` is a DIFFERENT, staying function — do not touch.)
  Callers inside the still-unmoved 1901-3532 region reference these names locally until their own move in Tasks 2-3 — so Task 1 must move the whole team-go list above at once (no partial split), and Tasks 2-3 add module-scope imports in their new modules.
- [ ] Run `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move team-go orchestration to witnessd.cli.team_go`

### Task 2: team ops cluster → `witnessd/cli/team_ops.py`

- [ ] Move verbatim: `_cmd_team_run` (1901), `_cmd_team_init` (1967), `_team_run_state_root` (3114), `_team_run_lane_state_root` (3124), `_codex_specs_are_isolated` (3132), `_parse_team_merge_group` (3163), `_cmd_team_plan_run` (3177), `_team_plan_state_root` (3254), `_is_inside_or_equal` (3264), `_paths_overlap` (3272), `_seed_codex_auth` (3276), `_cmd_a2_observer_run` (3288), `_print_trust_anchor_summary` (3339), `_lane_packet_to_run_team_spec` (3352), `_cmd_team_ledger` (3384), `_cmd_lane_exec` (3404), `_cmd_team_resume_audit` (3410), `_cmd_team_resume` (3423), `_cmd_team_kill` (3447), `_parse_team_lane` (3472), `_default_team_lane_command` (3521).
- [ ] `team_ops.py` module scope: stdlib `argparse, hashlib, json, os, shlex, shutil, sys`, `from pathlib import Path`; `from witnessd.cli._output import _emit_orro_error` (+ any other `_output` name the bodies use — grep); `from witnessd.cli.team_go import _apply_lane_prompt_files, _fill_interactive_team_init_args`; `from witnessd.status import render_status`; `from witnessd.trust_anchor import TrustAnchor`.
- [ ] `_cmd_team_kill` currently contains PR1's `from witnessd.cli.runtime_ops import _cmd_kill` call-site import — it moves verbatim WITH that line (still correct from team_ops).
- [ ] `__main__` module-scope import cleanup: `TrustAnchor` (line 45) is now unused by staying code (sole use was `_print_trust_anchor_summary`'s annotation) → delete it (grep to confirm zero remaining uses). `render_status` stays (used by `_cmd_run` :222, `_cmd_run_adapter` :803).
- [ ] Rewire parsers: `a2-observer-run` (3697), `team init` (3962), `team run` (4026), `team plan-run` (4065), `team-ledger` (4072), `team resume-audit` (4080), `team resume` (4088), `team kill` (4095), `lane-exec` (4100) → `_cli_handler("team_ops", ...)`.
- [ ] Amendment call-site imports in STAYING code:
  - `_cmd_run` :225 → `from witnessd.cli.team_ops import _print_trust_anchor_summary`
  - `_cmd_run_goal` :479 → `from witnessd.cli.team_ops import _lane_packet_to_run_team_spec`
  - `_role_lane_plan_team_specs` :675 → `from witnessd.cli.team_ops import _default_team_lane_command`
- [ ] Test-pin repointing (no re-exports): `tests/test_w15_parallel_execution.py:22` (`_codex_specs_are_isolated`), `tests/test_team_adapter_wiring.py:12` (`_parse_team_lane`, `_parse_team_merge_group`), `tests/test_planner.py:11` (`_parse_team_lane`) — change the import source to `witnessd.cli.team_ops` (keep importing `main` from `witnessd.__main__`).
- [ ] Run `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move team run/init/plan-run/resume/a2/ledger to witnessd.cli.team_ops`

### Task 3: orro-flow cluster → `witnessd/cli/flow.py`

- [ ] Move verbatim: `_cmd_orro_flow` (2019), `_run_orro_flow` (2041, ~450 lines), `_invoke_orro_flow_phase` (2495), `_orro_flow_phase_error` (2503), `_emit_orro_flow_blocker` (2524), `_orro_flow_flowplan_command` (2544).
- [ ] `flow.py` module scope: stdlib `argparse, json, os, shlex, sys, tempfile, time`, `from pathlib import Path`; `from witnessd.cli._output import _json_or_text, _structured_error` (grep for exact set); `from witnessd.cli.team_go import _invoke_cli_capture`; `from witnessd.cli.team_ops import _paths_overlap`.
- [ ] Rewire parser: `orro_flow.set_defaults(func=_cmd_orro_flow)` (3905) → `_cli_handler("flow", "_cmd_orro_flow")`.
- [ ] `tests/test_orro_flow.py`'s six `patch("witnessd.__main__.Path.cwd")` sites (104, 181, 252, 347, 404, 460) patch the `cwd` attribute on the shared `pathlib.Path` class object, so they keep binding after the move as long as `__main__` retains `from pathlib import Path` (it does — staying code uses it). **Leave them untouched**, but run `$RUN tests.test_orro_flow` explicitly and confirm all its tests still exercise the moved flow (a wrong-namespace patch here would surface as a test failure, since `_run_orro_flow` calls `Path.cwd()` on every run).
- [ ] Run `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move orro-flow orchestration to witnessd.cli.flow`

### Task 4: full verification

- [ ] `$RUN discover -s tests` → 821 OK, 17 skipped; `self-test --all` → 24/24; `python3 -m compileall witnessd` clean.
- [ ] `wc -l witnessd/__main__.py witnessd/cli/*.py` — `__main__` should drop by ~1,630 lines (the 1901-3532 region) to roughly 2,780.
- [ ] Print summary: commits, suite tail, line counts.

## Notes

- No module-level constants move (`DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS`, `ERR_ORRO_REFERENCE_ADAPTER_REFUSED`, `PROOFCHECK_WORKFLOW_ARTIFACTS` are used only by staying/parser code).
- No moving body references any staying `__main__` function except via `witnessd.cli._output` — verified by recon; if you find a counterexample, STOP and record it in `DEVIATIONS.md`.
