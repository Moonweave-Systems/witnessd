# CLI Split PR3 — Final: run/verify/plan/self-test + dead-import prune

> Design authority: `docs/plans/2026-07-18-cli-monolith-split-design.md`. All PR1/PR2 hard rules and amendments apply (verbatim moves; call-site lazy-import mechanism; no re-exports; no renames; STOP + `DEVIATIONS.md` on contradiction). Line anchors: main @ 819f274 (`__main__.py` = 2,858 lines).

**Test command:** `RUN='env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest'` — baseline 821 OK, 17 skipped. Full suite green before every commit.

**Design-doc reconciliation (approved):** the design's `cli/team_specs.py` is dropped — two of its four intended functions already live in `team_ops.py` (PR2); the remaining two (`_role_lane_plan_team_specs`, `_attach_role_capability_team_fields`) fold into `cli/run.py` (sole caller `_cmd_run_goal`). `_build_parser` stays intact (pure argparse wiring, zero handler references after this PR beyond `_cli_handler` closures); the projected final `__main__` is ~830 lines, not ~700 — the parser (585 lines) dominates and decomposing it adds nothing. Task 6 updates the design doc to record both.

### Task 1: run/proofrun cluster → `witnessd/cli/run.py`

- [x] Move verbatim (with `ERR_ORRO_REFERENCE_ADAPTER_REFUSED` constant, :57): `_cmd_run` (69), `_cmd_run_goal` (230), `_default_w18_packets` (548), `_proofrun_reference_adapter_warning` (572), `_reference_adapter_markers` (606), `_stamp_reference_adapter_artifact` (614), `_role_lane_plan_packets` (628), `_role_lane_plan_team_specs` (657), `_attach_role_capability_team_fields` (715), `_cmd_run_adapter` (747), `_adapter_proofrun_next_command` (819).
- [x] `run.py` module scope: the stdlib names the bodies reference (grep — descriptive, not exhaustive) + `from witnessd.cli._output import _emit_orro_error, _write_json_file` (grep for the exact subset) + `from witnessd.observer import ObserverSeparationError, assert_separated` and `from witnessd.status import render_status` if (and only if) the moved bodies use them (grep; `_cmd_run` uses both — `__main__` keeps its copies only while still used by staying code, else they prune in Task 5). Existing in-function `from witnessd.cli.team_go/team_ops import ...` lines move verbatim.
- [x] Rewire: `run` (2102) and `proofrun` (2124) `set_defaults` → `_cli_handler("run", "_cmd_run")`.
- [x] **Interim boundary import** (same mechanism as PR2 Amendment 3): `_cmd_proofcheck` stays until Task 2 but calls two Task-1-moved helpers at :1473/:1475 — add ONE in-function lazy import immediately before first use: `from witnessd.cli.run import _reference_adapter_markers, _stamp_reference_adapter_artifact`. The line travels verbatim when `_cmd_proofcheck` moves in Task 2 (and then Task 2 does NOT also add these two to `verify.py`'s module-scope imports).
- [x] Test migrations (same commit): `tests/test_orro_workflow.py` — 7 import sites of `_role_lane_plan_team_specs` (1267, 1294, 1340, 1396, 1528, 1556, 1583) → `from witnessd.cli.run import _role_lane_plan_team_specs`; `tests/test_w18_dx.py:121` `witnessd_cli._cmd_run_goal` (inspect.getsource) → `from witnessd.cli import run` + `run._cmd_run_goal` (file already imports `runtime_ops` the same way).
- [x] `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move run/proofrun execution cluster to witnessd.cli.run`

### Task 2: proofcheck/handoff/doctor/engine-lock → `witnessd/cli/verify.py`

- [x] Move verbatim (with `PROOFCHECK_WORKFLOW_ARTIFACTS`, :59-66): `_emit_orro_engine_lock_check_error` (1175), `_collect_orro_artifact_hashes` (1196), `_proofcheck_binding` (1217), `_advisory_provenance_home` (1230), `_advisory_provenance_blocked` (1238), `_run_advisory_provenance_verify` (1258), `_optional_advisory_provenance_verify` (1313), `_cmd_proofcheck` (1324), `_proofcheck_workflow_contract` (1480), `_cmd_advisory_provenance_check` (1515), `_cmd_handoff` (1532), `_cmd_orro_doctor` (1703), `_cmd_orro_engine_lock` (1816). (`_flowplan_role_lane_error_details` is a PLAN helper — Task 3, not here.)
- [x] `verify.py` module scope: stdlib per grep (`subprocess` is required — `_cmd_orro_doctor` :1775 uses `subprocess.run`) + `from witnessd.cli._output import _emit_orro_error, _depone_subprocess_env, _run_depone_json, _hash_file` (grep exact subset) (the `_reference_adapter_markers`/`_stamp_reference_adapter_artifact` cross-cluster imports arrive as the in-function line traveling with `_cmd_proofcheck`'s body from Task 1 — do not duplicate them at module scope). Existing in-function team_go import moves verbatim.
- [x] Rewire: `proofcheck` (2179), `advisory-provenance-check` (2188), `handoff` (2199), `orro-doctor` (2227), `engine-lock` (2237) → `_cli_handler("verify", ...)`.
- [x] Test migrations (same commit): `tests/test_orro_public_flow.py:2334, 2377, 2407` `patch("witnessd.__main__._run_depone_json")` → `patch("witnessd.cli.verify._run_depone_json")` (usage site is `_cmd_proofcheck`); `tests/test_orro_workstyle.py:102` `patch("witnessd.__main__.subprocess.run")` → `patch("witnessd.cli.verify.subprocess.run")` (drives `orro doctor`).
- [x] `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move proofcheck/handoff/doctor/engine-lock to witnessd.cli.verify`

### Task 3: plan/flowplan cluster → `witnessd/cli/plan.py`

- [x] Move verbatim: `_cmd_plan` (851), `_draft_prompt` (1049), `_flowplan_role_lane_error_details` (1071).
- [x] `plan.py` module scope: stdlib per grep + `from witnessd.cli._output import _emit_orro_error` (grep exact subset).
- [x] Rewire: `plan` (2150), `flowplan` (2157) → `_cli_handler("plan", "_cmd_plan")`.
- [x] `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move plan/flowplan compiler surface to witnessd.cli.plan`

### Task 4: self-test + stranded runtime_ops helpers

- [x] Move `_cmd_self_test` (1979) verbatim → new `witnessd/cli/self_test.py`; rewire `self-test` (2597) → `_cli_handler("self_test", "_cmd_self_test")`.
- [x] Move `_count_pending` (832) and `_derive_runlog_liveness` (1058) verbatim → `witnessd/cli/runtime_ops.py` (their ONLY callers live there), then **delete** `witnessd/cli/runtime_ops.py:9`'s `from witnessd.__main__ import _count_pending, _derive_runlog_liveness` back-import. `_derive_runlog_liveness` needs `_read_runlog` — already imported in runtime_ops from `_output`.
- [x] `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): move self-test and stranded runlog helpers out of __main__`

### Task 5: patch-target migration THEN dead-import prune (order matters)

- [x] FIRST migrate the six `patch("witnessd.__main__.Path.cwd", ...)` sites in `tests/test_orro_flow.py` (104, 181, 252, 347, 404, 460) → `patch("witnessd.cli.flow.Path.cwd", ...)` — the real `Path.cwd()` call is `witnessd/cli/flow.py:42`; today these pass only because `__main__.Path` is the shared `pathlib.Path` class object. Run `$RUN tests.test_orro_flow` → green.
- [x] THEN prune `__main__`'s now-dead module-scope imports: everything except `argparse` and `sys` should be deletable — grep each name (`hashlib, io, json, os, subprocess, shutil, shlex, tempfile, time, redirect_*, Path`, the whole `from witnessd.cli._output import (...)` block, `ObserverSeparationError/assert_separated`, `render_status`) against the staying code and delete the unused ones. `DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS` stays (parser :2499).
- [x] `$RUN discover -s tests` → 821 OK (a wrong prune surfaces here as AttributeError in the migrated patches or NameError in staying code). Commit: `refactor(cli): migrate flow patch targets and prune dead __main__ imports`

### Task 6: docs + full verification

- [x] Update `docs/plans/2026-07-18-cli-monolith-split-design.md`: record the team_specs fold-into-run decision and the realistic ~830-line end state (parser intact, decomposition not needed).
- [x] `$RUN discover -s tests` → 821 OK, 17 skipped; `self-test --all` → 24/24; `python3 -m compileall witnessd` clean.
- [x] `wc -l witnessd/__main__.py witnessd/cli/*.py`; expect `__main__` ≈ 830.
- [x] Print summary: commits, suite tail, line counts. Commit: `docs(plan): mark CLI split PR3 complete`
