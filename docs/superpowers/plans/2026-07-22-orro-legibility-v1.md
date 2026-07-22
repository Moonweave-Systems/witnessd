# ORRO Legibility v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add honest roadmap-bound project status and safe worktree inventory/cleanup to the witnessd-hosted ORRO surface.

**Architecture:** Keep roadmap persistence and binding validation in a focused `witnessd.orro_roadmap` module. Thread an optional explicit roadmap item into existing proofrun callers, then build status and tidy as one read/report-oriented CLI module that delegates run state to `decide_next` and live Git state to subprocess calls. Preserve all existing proof, approval, and assurance boundaries.

**Tech Stack:** Python 3 standard library, `argparse`, `unittest`, existing witnessd ORRO CLI and Depone-backed proofcheck seams.

---

### Task 1: Roadmap ledger and run binding

**Files:**
- Create: `witnessd/orro_roadmap.py`
- Create: `tests/test_orro_roadmap.py`

- [ ] **Step 1: Write failing ledger tests**

Define tests for absent-ledger `None`, a valid read/write round trip, malformed kind/schema/items/IDs/optional fields, duplicate IDs, and kebab-only IDs. The wished-for API is:

```python
read_roadmap(repo: Path) -> dict[str, Any] | None
write_roadmap(repo: Path, roadmap: dict[str, Any]) -> Path
```

- [ ] **Step 2: Run the ledger tests and verify RED**

Run: `PYTHONPATH=/home/ubuntu/depone /usr/bin/python3 -m unittest tests.test_orro_roadmap -v`

Expected: import failure for missing `witnessd.orro_roadmap`.

- [ ] **Step 3: Implement strict ledger validation and canonical writes**

Use `kind: orro-roadmap`, `schema_version: 0.1`, unique kebab IDs, non-empty titles, optional `status` restricted to `done`, and optional string `note`/`spec`. Write sorted, indented JSON with a trailing newline after lazy `.orro` creation.

- [ ] **Step 4: Add failing binding tests**

Define tests for `seal_roadmap_binding(repo=..., run_dir=..., item_id=...)`, `read_roadmap_binding(run_dir)`, stable ledger SHA-256, unknown item error code `ERR_ORRO_ROADMAP_ITEM_UNKNOWN`, malformed bindings, and seal-then-readable behavior.

- [ ] **Step 5: Run binding tests and verify RED, then implement GREEN**

Run the same focused command. Implement binding JSON with exactly `kind`, `schema_version`, `item_id`, `ledger_path`, and `ledger_sha256`; validate the written file by reading it back before returning.

- [ ] **Step 6: Re-run focused tests and commit**

Commit message: `feat: add ORRO roadmap ledger bindings` with the required co-author trailer.

### Task 2: Explicit roadmap item propagation

**Files:**
- Modify: `witnessd/__main__.py`
- Modify: `witnessd/cli/run.py`
- Modify: `witnessd/cli/flow.py`
- Modify: `witnessd/cli/team_go.py`
- Modify: `witnessd/cli/companion.py`
- Modify: `tests/test_orro_command_surface.py`
- Modify: `tests/test_orro_public_flow.py`
- Modify: `tests/test_orro_flow.py`
- Modify: `tests/test_orro_check.py`

- [ ] **Step 1: Write parser and proofrun RED tests**

Assert `--roadmap-item <id>` parses on `proofrun`, `orro-flow`, `team go`, and `orro-check`; an unknown item exits 2 with structured `ERR_ORRO_ROADMAP_ITEM_UNKNOWN` before run execution; a known item creates `roadmap-binding.json` in the chosen run directory.

- [ ] **Step 2: Run focused tests and verify RED**

Run the four named test modules with `/usr/bin/python3 -m unittest ... -v` and the required Depone environment.

- [ ] **Step 3: Implement proofrun validation/sealing**

Validate the explicitly supplied item against `<repo>/.orro/roadmap.json` before creating/executing the run, then seal into the run directory. Do nothing when the flag is absent; never infer an item.

- [ ] **Step 4: Thread the flag through existing composed commands**

Append `--roadmap-item <id>` only when explicitly supplied to the proofrun argv built by guided flow, team go, and check. Keep explicit caller values unchanged.

- [ ] **Step 5: Re-run focused tests and commit**

Commit message: `feat: bind proofruns to explicit roadmap items` with the required co-author trailer.

### Task 3: Status reporting

**Files:**
- Create: `witnessd/cli/status.py`
- Create: `tests/test_orro_status.py`
- Modify: `witnessd/__main__.py`
- Modify: `orro/__main__.py`
- Modify: `tests/test_orro_command_surface.py`

- [ ] **Step 1: Write status RED tests**

Cover absent ledger, malformed ledger exit 2, `done (verified)` for complete or passing ready-for-handoff runs with evidence reference, `in-progress` using the latest bound run and its `decide_next` state, exact `marked-done (unverified)`, `not-started`, unbound newest-first off-plan runs, workspace counts/size/dirty signal, JSON shape, human boundary line, and report exit 0 for blocked runs.

- [ ] **Step 2: Run status tests and verify RED**

Run: `PYTHONPATH=/home/ubuntu/depone /usr/bin/python3 -m unittest tests.test_orro_status -v`

- [ ] **Step 3: Implement status from observed artifacts**

Enumerate `<home>/runs/*/`, load optional bindings fail-soft, call `decide_next(run_dir, home=home)` for every run, derive only the locked vocabulary, attach proofcheck/handoff evidence paths for verified completion, and render JSON or concise human text with the non-proof boundary.

- [ ] **Step 4: Register command and synchronize help**

Add internal parser `orro-status`, map public `status`, and update witnessd-side `ORRO_HELP` plus authoritative command-surface tests.

- [ ] **Step 5: Re-run focused tests and commit**

Commit message: `feat: add honest ORRO roadmap status` with the required co-author trailer.

### Task 4: Tidy inventory and safe cleanup

**Files:**
- Modify: `witnessd/cli/status.py`
- Create: `tests/test_orro_tidy.py`
- Modify: `witnessd/__main__.py`
- Modify: `orro/__main__.py`
- Modify: `tests/test_orro_command_surface.py`

- [ ] **Step 1: Write tidy RED tests**

Create temporary Git repositories/worktrees and assert dry-run performs no mutation; inventory reports branch, base/head, live dirty state, size, and owner `decide_next` state; registered worktrees outside runs are visible; apply keeps dirty and non-complete worktrees with exact reasons; apply removes only clean complete worktrees without `--force`; missing registered paths are pruned; run directories remain.

- [ ] **Step 2: Run tidy tests and verify RED**

Run: `PYTHONPATH=/home/ubuntu/depone /usr/bin/python3 -m unittest tests.test_orro_tidy -v`

- [ ] **Step 3: Implement inventory and apply policy**

Use live `git status --porcelain`, `git worktree list --porcelain`, `git worktree remove <path>` without force, and one `git worktree prune` after eligible removals/prunable missing registrations. Render every non-removal as `kept: <reason>` and never delete run directories.

- [ ] **Step 4: Register command and synchronize help**

Add internal parser `orro-tidy`, public map entry `tidy`, help entries, and absence-of-force assertions.

- [ ] **Step 5: Re-run focused tests and commit**

Commit message: `feat: add safe ORRO worktree tidy` with the required co-author trailer.

### Task 5: Live and full verification

**Files:**
- No production changes unless a failing behavior first receives a regression test.

- [ ] **Step 1: Run targeted regression suites**

Run the roadmap, public flow, guided flow, check, status, tidy, and command-surface test modules with `/usr/bin/python3` plus `PYTHONPATH` and `WITNESSD_DEPONE_ROOT`.

- [ ] **Step 2: Run the locked live scenario in retained `mktemp` directories**

Seed a ledger; complete a bound verification-only proofrun and proofcheck/handoff path; create an unbound run; prove human status displays `done (verified)` and its evidence reference; prove tidy keeps a dirty worktree and removes a clean complete worktree with `--apply`.

- [ ] **Step 3: Run clean-environment full suite**

Run exactly:

```bash
PYTHONNOUSERSITE=1 PYTHONPATH=/home/ubuntu/depone WITNESSD_DEPONE_ROOT=/home/ubuntu/depone /usr/bin/python3 -m unittest discover -s tests
```

Capture output in `/tmp/leg.log`, read the `Ran` and `OK`/failure summary, and compare any failures against clean `main` only if necessary.

- [ ] **Step 4: Run static completion checks**

Run `git diff --check`, `/usr/bin/python3 -m compileall witnessd orro tests`, command help smokes, `git status --short`, and inspect every commit trailer. Do not push, tag, release, or re-pin.
