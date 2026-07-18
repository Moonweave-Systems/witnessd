# AI-Adapter Cache Env Shaping (Design)

> Fixes the ignore-blind snapshot false-positive for AI adapters (recon: `project_adapter_merkle_gitignore_gap`). Same principle as #133 (shell): never filter evidence; shape the execution environment. Anchors: main @ 71df553.

**Bug:** AI adapters compute a lane's `touched_files` from an ignore-blind filesystem snapshot (`changeset.capture_snapshot`). When an agent runs `ruff`/`pytest`/`mypy`, the resulting `.ruff_cache`/`.pytest_cache`/`__pycache__`/`.mypy_cache` land in `touched_files` even though `git` shows only the real edit. This false-positives against write-scope/forbidden-file gates (Depone reads the snapshot-derived `git-diff-name-only.txt`) and, most acutely, against the review-lane read-only exit-125 assertion (`agy.py:681`, `claude.py:320`).

**Principle (unchanged):** don't filter observed changes (`.gitignore` filter is rejected — it undercuts the A1/A2 completeness claim, lets an agent evade the read-only gate via a gitignored path, and `.gitignore` is attacker-controlled). Instead prevent the incidental writes at the source by redirecting tool caches **outside the lane worktree**. Observation stays complete; enforcement stays strict; no hiding place.

## The env overlay

A shared builder produces a cache-redirect overlay, pointed at a per-lane dir asserted-separated from the worktree (reuse the `CODEX_HOME` precedent: under the `StateNamespace.state_dir`, which `adapter_run.py:288` already `assert_separated`s from the worktree):

```
cache_dir = <state_dir>/adapter-cache/<task_id>   # mkdir -p, outside worktree
PYTHONDONTWRITEBYTECODE = "1"
PYTHONPYCACHEPREFIX     = cache_dir/pycache
RUFF_CACHE_DIR          = cache_dir/ruff
MYPY_CACHE_DIR          = cache_dir/mypy
PYTEST_ADDOPTS          = "-o cache_dir=<cache_dir/pytest>"   # appended to any ambient value, space-joined
```

All five are **pure redirects / suppression** — no tool behavior is disabled (pytest `-o cache_dir=` moves the dir, does not turn off caching). **Honest limitation (documented, not overclaimed):** best-effort over the Python-dev ecosystem; an agent invoking other toolchains (node/cargo/go) can still create caches — those remain honestly observed and enforced. This is the same best-effort shape as #133.

## Wiring (two invocation paths, one shared helper)

New shared helper (location: extend `StateNamespace` in `witnessd/state.py`, matching `codex_env`): `adapter_cache_env(task_id, base_env=None) -> dict[str,str]` returns `{**(base_env or os.environ), **overlay}`. `codex_env` is extended to merge the same overlay (so codex keeps `CODEX_HOME`+auth seeding AND gains the ruff/pytest/mypy redirects — it currently only sets `PYTHONDONTWRITEBYTECODE`).

**Path A — execution lanes** (`adapter_run.run_adapter_lane` → `_run_adapter`):
- Build `adapter_env = namespace.adapter_cache_env(task_id)` for non-codex adapters at `adapter_run.py:346` (where `codex_env` is built today).
- Thread it through `_run_adapter` (`adapter_run.py:96`) to every branch:
  - claude: add `env` param to `run_claude_lane` (`claude.py:418`) + pass to both its `subprocess.run` sites (execute `:476`, critic `:240`).
  - opencode: add `env` param to `_run_cli_lane` (`base.py:199`) + pass to `subprocess.run` (`:219`).
  - agy: pass `env=adapter_env` into `run_agy_review_lane` (param already exists → `_run_pty_command` → `_merged_env`, `agy.py:530`).
  - gemini: pass `env=adapter_env` into `run_gemini_review_lane` (param already exists).

**Path B — review lanes** (`orro_review.run_review_role_lane_plan` → `run_agy_review_lane`/gemini directly, bypasses `adapter_run`): build the same `adapter_cache_env` where the review lane's scratch/state is available and pass it into the review-lane call. (This is the path that produces the reported exit-125 false positive.)

## Invariants preserved

- `capture_snapshot` is **not** modified — observation stays complete/ignore-blind; we only change what the agent's tools write, not what we record.
- Review-lane exit-125 read-only gate (`agy.py:678-680` "Do not weaken this to an allowlist") is untouched — still fails on ANY real touched file; we only stop incidental cache writes from ever occurring.
- Write-scope / forbidden-file enforcement unchanged.
- codex `CODEX_HOME` + `_seed_codex_auth_from_ambient_home` preserved (overlay is merged INTO `codex_env`, never replaces it).

## Tests (TDD, #133 pattern)

- Unit: `namespace.adapter_cache_env(task_id)` returns the five keys pointing under `state_dir/adapter-cache/<task_id>` and separated from a given worktree; `codex_env` now also carries the ruff/pytest/mypy keys while keeping `CODEX_HOME`+auth (extend `tests/test_state_isolation.py`).
- Per-adapter env reaches the process: via the fake-binary-echoes-env seam (`_fake_codex_writes_env_and_code`, `tests/test_adapter_run.py:108`) — extend/add fakes for claude/opencode/agy that echo `$PYTHONPYCACHEPREFIX`/`$RUFF_CACHE_DIR` to a file; assert the lane's isolated cache dir wins.
- **Teeth preserved**: a review lane whose fake agent writes a real tracked file still → exit 125 / touched non-empty (`tests/test_agy_adapter.py:528` stays green).
- End-to-end regression pin (the reported case): a lane whose adapter (fake) creates `.ruff_cache/`/`__pycache__/` **inside the worktree via a tool that honors the redirect env** produces empty touched for the cache paths — i.e. prove the redirect actually keeps them out. (A fake that writes to `$RUFF_CACHE_DIR` instead of `./.ruff_cache` demonstrates the mechanism deterministically.)

## Verification (mine)

Clean-env full suite; mutation (drop the overlay → env-reaches-process tests fail; drop the review-path wiring → review-lane cache test fails); **live smoke with real adapters** ([[reference-fake-binary-masking]] — fakes can't prove real CLIs honor the env): run a real `claude`/`codex` lane that invokes `ruff`/`pytest` in a no-`.gitignore` repo and confirm `touched_files` has no `.ruff_cache`/`__pycache__`, and a real agy review lane no longer false-fails on tool cache.

## Scope

In: the five-var Python-ecosystem overlay + both invocation paths + all execution/review adapters. Out (separate follow-up): the `git-diff-name-only.txt` misnomer (snapshot masquerading as git; Depone reads it as git truth — a contract-adjacent honesty cleanup, and after this fix it no longer carries a live false positive).
