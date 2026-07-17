# ORRO Flow Guided Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one `orro flow` command that safely threads init, scout, flowplan, proofrun, and proofcheck while returning one structured result or first-phase blocker.

**Architecture:** Keep orchestration in `witnessd.__main__` and invoke the existing CLI phase functions through the existing capture seam. Build an omitted rolepack with the existing team-init builder, preserve the user-provided write scopes exactly, pass `--model-policy default`, keep the runner sandbox separate from observer artifacts, and normalize every phase failure into the existing layer-2 structured-error fields.

**Tech Stack:** Python standard library, existing witnessd CLI/library functions, `unittest`, OpenSSL-backed existing signing paths.

---

### Task 1: Lock the CLI and blocker contracts with failing tests

**Files:**
- Create: `tests/test_orro_flow.py`
- Modify: `tests/test_orro_packaging.py`

- [ ] Add a parser/help test proving `flow` is public and accepts `goal`, repeated `--write-scope`, `--adapter`, `--runner-sandbox`, `--rolepack-file`, `--role-lane-tier`, `--run-dir`, `--allow-reference-adapter`, and `--json`.
- [ ] Add a missing-write-scope invocation that asserts nonzero exit, `kind: orro-flow-result`, `decision: blocked`, `blocked_phase`, and all four actionable error fields without `Traceback` in either stream.
- [ ] Add a risky-goal invocation that asserts the existing risky-change gate is surfaced before `team-ledger.json` exists.
- [ ] Add a shell reference-adapter flow test using the pinned Depone test checkout and `--allow-reference-adapter`; assert all five phase statuses, `run_dir`, final proofcheck decision, exact scope retention in the generated rolepack, and separate observer/runner paths.
- [ ] Run `PYTHONPATH=/tmp/depone-orro-flow.AxY0Qg WITNESSD_DEPONE_ROOT=/tmp/depone-orro-flow.AxY0Qg PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_orro_flow tests.test_orro_packaging` and confirm the new tests fail because `flow` is absent.

### Task 2: Implement the minimal orchestrator

**Files:**
- Modify: `witnessd/__main__.py`
- Modify: `witnessd/orro_team_surface.py` only if the existing builder needs a narrow adapter-preserving input seam

- [ ] Add `_cmd_orro_flow(args: argparse.Namespace) -> int` and small helpers for phase invocation/result normalization.
- [ ] Validate `--write-scope`, `--adapter`, and sandbox separation before execution; emit `orro-flow-result` blockers using `_structured_error` rather than raising.
- [ ] Invoke existing init and scout commands, scaffold an omitted rolepack with `build_rolepack_scaffold`/`write_rolepack_scaffold`, then invoke existing flowplan, proofrun, and proofcheck commands with generated artifact paths.
- [ ] Force flowplan `--model-policy default`, pass the chosen adapter through the rolepack, pass `--runner-sandbox` to proofrun, and pass `--allow-reference-adapter` only when explicitly supplied.
- [ ] Stop on the first failing phase and retain the blocked phase's existing structured remediation when available; otherwise generate the exact single-phase retry command.
- [ ] Catch phase exceptions at the orchestration boundary and convert them to actionable blockers so no traceback escapes.
- [ ] Run the focused tests until green.

### Task 3: Wire the public ORRO surface and truthful docs

**Files:**
- Modify: `witnessd/__main__.py`
- Modify: `orro/__main__.py`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `CLAUDE.md`

- [ ] Add the parser with `set_defaults(func=_cmd_orro_flow)` and add `flow` to `ORRO_COMMAND_MAP`/`ORRO_COMMANDS`.
- [ ] Add `flow` to the witnessd-hosted `orro --help` command list and describe it as gated orchestration, not a new verifier or assurance source.
- [ ] Document required `--write-scope`/`--adapter`, automatic artifact threading, structured blocker behavior, explicit reference-adapter opt-in, and the no-gate-bypass boundary.
- [ ] Run focused parser, packaging, and flow tests.

### Task 4: Verify, review, and publish

**Files:**
- Review all changed files; no Depone or ORRO repository files are in scope.

- [ ] Run `git diff --check` and `python3 -m compileall witnessd orro tests`.
- [ ] Run the exact full suite with `PATH=/usr/bin:/bin`, `PYTHONPATH=/tmp/depone-orro-flow.AxY0Qg`, `WITNESSD_DEPONE_ROOT=/tmp/depone-orro-flow.AxY0Qg`, `PYTHONNOUSERSITE=1`, and `PYTHONDONTWRITEBYTECODE=1`; compare against the origin/main baseline of 791 tests, 17 skipped, zero failures.
- [ ] Run a focused review of the diff for gate weakening, scope widening, raw exception leaks, and missing tests.
- [ ] Commit the scoped diff, push `feat/orro-flow-guided-command`, and open the requested PR referencing #66 with the exact title and safety statements.
- [ ] Inspect GitHub PR metadata/check conclusions and report the PR URL plus any still-running remote checks truthfully.
