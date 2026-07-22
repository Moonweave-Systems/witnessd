# Code Health Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add append-only code-health configuration seeding, a persisted gate-tier profile, and explicit enforcement promotion to `orro check` without changing Depone or v2 behavior when the profile is absent.

**Architecture:** Keep configuration/profile persistence in `witnessd.health_detect`, using the existing header presence scan and stdlib JSON. Extend only the existing `orro check` parser and companion command so init/promote happen before the current health detection and execution pipeline; the resulting gate dictionaries continue through the unchanged v2 evidence and Depone verification path.

**Tech Stack:** Python 3.10-compatible stdlib, `argparse`, `json`, `pathlib`, `unittest`.

---

### Task 1: Lock append-only seeding and profile precedence

**Files:**
- Modify: `tests/test_health_detect.py`
- Modify: `witnessd/health_detect.py`

- [ ] **Step 1: Write failing tests** for a partial `[tool.ruff]` file that remains byte-identical while missing Black and mccabe blocks append, for a newly created `pyproject.toml`, and for `.orro/health.json` overriding detected gates and tiers.
- [ ] **Step 2: Run RED:** `/usr/bin/python3 -m unittest tests.test_health_detect -v`; expect failures because seeding/profile APIs do not exist.
- [ ] **Step 3: Implement minimal stdlib helpers:** append only absent section blocks, return `written` and `present`, write the three bootstrap gates as a JSON list, and have `detect_health_gates()` prefer that list when present while retaining the existing branch byte-for-byte when absent.
- [ ] **Step 4: Run GREEN:** `/usr/bin/python3 -m unittest tests.test_health_detect -v`; expect all tests to pass.

### Task 2: Lock init and promote CLI behavior

**Files:**
- Modify: `tests/test_orro_command_surface.py`
- Modify: `tests/test_orro_check.py`
- Modify: `witnessd/__main__.py`
- Modify: `witnessd/cli/companion.py`

- [ ] **Step 1: Write failing parser/command tests** proving `--init` seeds config/profile then continues into health execution, `--promote lint` changes only the persisted tier, missing profiles return `ERR_ORRO_HEALTH_NO_PROFILE`, and unknown gates block without rewriting the file.
- [ ] **Step 2: Run RED:** `/usr/bin/python3 -m unittest tests.test_orro_command_surface tests.test_orro_check -v`; expect failures for unknown flags/missing command behavior.
- [ ] **Step 3: Implement minimal parser and command wiring:** make init/promote imply health, perform writes before detection, validate all requested promotions before atomic profile replacement, attach a bootstrap report to JSON output, and print the safe one-time `--fix --apply` guidance in human output.
- [ ] **Step 4: Run GREEN:** `/usr/bin/python3 -m unittest tests.test_orro_command_surface tests.test_orro_check -v`; expect all tests to pass.

### Task 3: Verify and commit the focused change

**Files:**
- Verify: `witnessd/health_detect.py`, `witnessd/__main__.py`, `witnessd/cli/companion.py`, and focused tests.

- [ ] **Step 1: Run focused tests** with `/usr/bin/python3` and the required local Depone environment.
- [ ] **Step 2: Run static checks:** `/usr/bin/python3 -m compileall witnessd orro tests` and `git diff --check`.
- [ ] **Step 3: Commit focused changes** on `feat/code-health-bootstrap` with the required `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

### Task 4: Capture live bootstrap evidence and full-suite status

**Files:**
- Create only temporary repositories via `mktemp -d`; do not remove them destructively.

- [ ] **Step 1: Live bare-repo init:** run `orro check --health --init`, then capture `pyproject.toml`, `.orro/health.json`, and the health verdict showing gates ran.
- [ ] **Step 2: Live non-clobber:** preserve a pre-init copy and show a diff where the custom Ruff section is unchanged and only absent blocks append.
- [ ] **Step 3: Live promote:** use a deterministic failing Ruff executable to show lint advisory exit 0, promote lint, then show block exit 2 and the updated profile.
- [ ] **Step 4: Run the exact clean-env suite:** `PYTHONNOUSERSITE=1 PYTHONPATH=/home/ubuntu/depone WITNESSD_DEPONE_ROOT=/home/ubuntu/depone /usr/bin/python3 -m unittest discover -s tests > /tmp/w3.log 2>&1`; report exit, `Ran`, `OK`, or compare any failures with `main` before attribution.
