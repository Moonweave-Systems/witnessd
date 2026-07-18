# Verification Check Env Shaping (Design + Plan)

> Approved 2026-07-18. Fixes a live-reproduced false positive in the verification-only lane shipped in #128. Anchors: main @ b2c55f8.

**Bug (live-reproduced):** an honest verification check that merely imports Python code (`/usr/bin/python3 -c 'import pkg.mod; ...'`) writes `pkg/__pycache__/*.pyc` into the lane worktree; in a target repo whose `.gitignore` does not cover interpreter caches, `_commit_lane` commits it → `changed_files` non-empty → Depone falsifies the honest lane with `ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`. The flagship use case (`--check "pytest -q"`) walks exactly this path. With `.gitignore` coverage the lane is clean (`git add -A` skips ignored files) — verified both ways.

**Design principle (decided): never filter evidence; shape the execution environment.** Suppressing observed changes (e.g. dropping `.gitignore`-matched paths from `touched`) would weaken the falsification gate — `__pycache__/evil.pyc` becomes a hiding place. Instead, prevent the incidental write at the source: inject `PYTHONDONTWRITEBYTECODE=1` into the check subprocess environment **for verification-only lanes only**. Anything a check still writes remains an honest violation and keeps getting falsified. The 2026-07-11 idea of ignore-aware `touched` filtering is rejected.

## Changes

1. `witnessd/adapters/shell.py`: `_run_one(command, sandbox, *, command_runner=None, env=None)` passes `env=env` to `subprocess.run` (None = inherit, current behavior). `run_shell_lane(..., extra_env: dict[str, str] | None = None)` computes `env = {**os.environ, **extra_env}` once when `extra_env` is given (else None) and passes it to every `_run_one` call (commands and `test_command`). `command_runner` injection path is unaffected.
2. `witnessd/fanin.py` `_run_write_lane` (:1654 call): when `lane_intent == "verification-only"`, call `run_shell_lane(..., extra_env={"PYTHONDONTWRITEBYTECODE": "1"})`; otherwise exactly as today (no `extra_env`).
3. Docs: the verification-only paragraphs (SPEC3.md, CLAUDE.md, SKILL.md, docs/README.md — the ones revised in #128) gain one sentence: checks run with `PYTHONDONTWRITEBYTECODE=1`; checks must otherwise be side-effect-free, and tool caches (`.pytest_cache`, `.ruff_cache`, …) should be covered by the target repo's `.gitignore` or redirected outside the worktree — any file a check writes is honestly falsified by Depone.

## Tests (TDD; RUN='env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest', baseline 821 OK / 17 skipped)

- Unit (`tests/test_adapters_shell.py` or the file holding `run_shell_lane` tests — follow existing conventions): `run_shell_lane` with `extra_env={"WITNESSD_TEST_MARKER": "42"}` and command `["sh", "-c", "echo $WITNESSD_TEST_MARKER"]` → receipt stdout `42\n`; without `extra_env` → empty. (Deterministic mechanism proof, no Python-cache reliance.)
- fanin-level (`tests/test_team_fanin.py`, reuse its run_team harness): claimless verification-only lane whose check is `["sh", "-c", "echo bytecode=$PYTHONDONTWRITEBYTECODE"]` → its command receipt stdout contains `bytecode=1`. Regression pin: a normal write lane (region-claiming, no lane_intent) running the same echo does NOT see the injected value (stdout `bytecode=` + ambient-independent: assert the receipt does not contain `bytecode=1` **after** popping the var from a copied env… simpler: assert spec-level — the write lane's `run_shell_lane` call receives no `extra_env`; if that is awkward to observe, use monkeypatch on `run_shell_lane` capturing kwargs for both lanes).
- End-to-end integration (same harness, no-`.gitignore` repo seeded with an importable `pkg/mod.py`): verification-only lane with check `["/usr/bin/python3", "-c", "import sys; sys.path.insert(0, '.'); import pkg.mod"]` → lane pass, `touched_files == []`, Depone verdict pass. (Locally the ambient `PYTHONDONTWRITEBYTECODE=1` makes this trivially green; in CI, which does not set it, the injection is what keeps it green — both runs are meaningful.)
- Teeth preservation (may already exist — extend, don't duplicate): a verification-only check that writes a tracked-path file still → Depone `ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`.

## Out of scope

The AI-adapter Merkle `_diff_touched` surface (2026-07-11 original finding: `capture_snapshot` ignores `.gitignore`, review lanes' `touched` gets polluted when the agent runs ruff/pytest) — separate surface, separate decision, follow-up track.

## Verification (mine, post-implementation)

Clean-env full suite; mutation (a) drop the injection → mechanism tests fail, (b) inject unconditionally → write-lane pin fails; live smoke re-running today's reproduction with the wrapper's own `PYTHONDONTWRITEBYTECODE` **unset** (plain `python3 -c 'import pkg.mod'` check in a no-gitignore repo → pass/touched [], then `echo x > f.txt` check → blocked MUTATED).
