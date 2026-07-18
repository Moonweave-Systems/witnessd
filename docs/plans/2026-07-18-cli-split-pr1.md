# CLI Split PR1 — Foundation + Least-Entangled Clusters

> Design authority: `docs/plans/2026-07-18-cli-monolith-split-design.md`. Line anchors: main @ 1da6907 (= branch base). Every move is **verbatim** — function bodies byte-identical, only import lines may change. Run the suite between tasks.

**Test command:** `RUN='env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest'` — full suite baseline 821 OK, 17 skipped.

### Task 1: `witnessd/cli/` package + `_output.py` + dispatch helper

**Files:** Create `witnessd/cli/__init__.py` (one-line docstring, no imports), `witnessd/cli/_output.py`. Modify `witnessd/__main__.py`.

- [x] Move verbatim from `__main__.py` to `witnessd/cli/_output.py` (module-scope imports: only the stdlib names these functions reference — `json`, `os`, `sys`, `hashlib`, `subprocess`, `argparse` as needed; in-function `witnessd.*` imports stay in-function):
  - `_depone_subprocess_env` (1164), `_run_depone_json` (1181), `_structured_error` (1208), `_emit_orro_error` (1331), `_read_runlog` (1103), `_hash_file` (1710), `_write_json_file` (4104), `_json_or_text` (4123)
- [x] In `__main__.py`, add at module scope (top, after stdlib imports):
  ```python
  from witnessd.cli._output import (
      _depone_subprocess_env,
      _emit_orro_error,
      _hash_file,
      _json_or_text,
      _read_runlog,
      _run_depone_json,
      _structured_error,
      _write_json_file,
  )
  ```
  (`_output` is stdlib-only at module scope, so this eager import is cheap and cycle-free.) All 60+ existing call sites in `__main__` keep working unchanged; existing `patch("witnessd.__main__._run_depone_json")` tests stay valid for handlers that remain in `__main__`.
- [x] Add the lazy dispatch helper to `__main__.py`:
  ```python
  def _cli_handler(module: str, name: str):
      def _invoke(args: argparse.Namespace) -> int:
          import importlib

          return getattr(importlib.import_module(f"witnessd.cli.{module}"), name)(args)

      return _invoke
  ```
- [x] Run: `$RUN discover -s tests` → 821 OK. Commit: `refactor(cli): hoist shared CLI output/dispatch helpers into witnessd.cli._output`

### Task 2: advisory/continuation cluster → `witnessd/cli/advisory.py`

- [x] Move verbatim (lines 2074-2558): `_cmd_orro_next` (2074), `_cmd_orro_advise` (2097), `_cmd_orro_sketch` (2124), `_cmd_orro_trace` (2172), `_cmd_orro_report` (2220), `_cmd_orro_review` (2256), `_cmd_orro_auto` (2289), `_run_orro_auto_step` (2493). Module-scope imports in `advisory.py`: the stdlib names these bodies use (`argparse`, `json`, `os`, `subprocess`, `sys`, `pathlib.Path` — grep the bodies) + `from witnessd.cli._output import _emit_orro_error, _run_depone_json, _structured_error, _json_or_text` (only the ones actually referenced).
- [x] Rewire in `_build_parser`: for each of the 7 commands (`orro-next` 4863, `orro-advise` 4870, `orro-sketch` 4878, `orro-trace` 4894, `orro-report` 4910, `orro-review` 4918, `orro-auto` 4937) replace `set_defaults(func=_cmd_orro_X)` with `set_defaults(func=_cli_handler("advisory", "_cmd_orro_X"))`.
- [ ] **Patch-target migration** (targets follow the usage-site namespace):
  - `tests/test_orro_report.py`: `patch("witnessd.__main__._run_depone_json")` → `patch("witnessd.cli.advisory._run_depone_json")`.
  - `tests/test_orro_workstyle.py`: `patch("witnessd.__main__.subprocess", ...)` → `patch("witnessd.cli.advisory.subprocess", ...)` (ensure `advisory.py` imports `subprocess` at module scope since the moved body references it).
  - `tests/test_orro_public_flow.py`'s three `patch("witnessd.__main__._run_depone_json")` sites exercise proofcheck/flow paths that REMAIN in `__main__` — leave them untouched; verify they still fail-if-broken by running that module.
  - Grep for any other `witnessd.__main__` patch/import referencing the moved names: `grep -rn "witnessd.__main__" tests/ | grep -E "next|advise|sketch|trace|report|review|auto"`.
- [x] Run: `$RUN tests.test_orro_report tests.test_orro_workstyle tests.test_orro_next tests.test_orro_auto tests.test_orro_public_flow` (adjust to the actual test-module names; then the full suite). Commit: `refactor(cli): move advisory/continuation handlers to witnessd.cli.advisory`

### Task 3: pilot cluster → `witnessd/cli/pilot.py`

- [x] Move verbatim (827-897): the five `_cmd_pilot_*` handlers. Rewire the `pilot` nested subparsers (5221+) via `_cli_handler("pilot", ...)`.
- [x] `grep -rn "witnessd.__main__" tests/ | grep -i pilot` → migrate any hits. Run pilot-related tests + full suite. Commit: `refactor(cli): move pilot handlers to witnessd.cli.pilot`

### Task 4: lifecycle cluster → `witnessd/cli/runtime_ops.py`

- [ ] Move verbatim: `_cmd_status` (812), `_cmd_verify` (1119), `_cmd_doctor` (2559), `_cmd_isolation` (2581), `_cmd_faultkit` (2591), `_cmd_pause` (2667), `_cmd_resume_pause` (2680), `_cmd_kill` (2693), `_cmd_learn` (2723), `_cmd_install` (2762). (NOT `_cmd_orro_doctor` — that is the Depone-dispatch cluster, PR3.) `_cmd_status` uses the eager `from witnessd.status import render_status` — move that import to `runtime_ops.py` module scope and delete it from `__main__` if no remaining `__main__` code uses it (grep first).
- [ ] Rewire parsers: `status` 4783, `verify` 4788, `doctor` 4835, `isolation` 4977, `faultkit` 4981+, `pause` 5172, `resume` 5177, `kill` 5183, `learn promote` 5189, `install`/`upgrade` 5203.
- [ ] Patch/import migration: `grep -rn "witnessd.__main__" tests/ | grep -E "status|verify|doctor|isolation|faultkit|pause|resume|kill|learn|install"` → migrate hits whose handlers moved. Run those modules + full suite. Commit: `refactor(cli): move lifecycle/runtime handlers to witnessd.cli.runtime_ops`

### Task 5: bootstrap cluster → `witnessd/cli/bootstrap.py`

- [x] Move verbatim: `_cmd_init` (2795), `_cmd_orro_setup` (2827), `_cmd_scout` (2922), `_cmd_route` (2936). Rewire: `init` 4687, `orro-setup` 4701, `scout` 4717, `route` 4825.
- [x] Patch/import migration grep as above (`init|setup|scout|route`). Run + full suite. Commit: `refactor(cli): move bootstrap handlers to witnessd.cli.bootstrap`

### Task 6: full verification

- [x] `$RUN discover -s tests` → 821 OK, 17 skipped (count must not drop).
- [x] `env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m witnessd self-test --all` → 24/24.
- [x] `python3 -m compileall witnessd` clean; `git diff --stat 1da6907..HEAD` — `__main__.py` shrinks by roughly the moved line count; no non-CLI module touched.
- [x] Print final summary: commits, suite tail, `wc -l witnessd/__main__.py witnessd/cli/*.py`.

## Hard rules

- Verbatim moves only. If a function turns out to need editing to move (hidden coupling), STOP that task and record it in `DEVIATIONS.md` with the exact coupling; do not improvise a redesign.
- Never leave a re-export of a moved name in `__main__` (except the 8 `_output` names imported for `__main__`'s own remaining call sites).
- `main`, `ORRO_COMMANDS`, `ORRO_COMMAND_MAP`, `_normalize_*` stay in `__main__` untouched.
- No command renames, no output changes, no new features, no docstrings added to moved code.
