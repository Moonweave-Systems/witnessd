# Claim Vocabulary Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and
> verification-before-completion task by task. Steps use checkbox syntax for
> tracking.

**Goal:** Add one internal seven-axis claim representation and migrate four
producer declarations plus their report projection without changing public
behavior.

**Architecture:** A new `witnessd.claim` module defines the finite axes and an
immutable `Claim`. Producer and verifier factories expose different signatures,
while declaration-specific functions project claims to the existing JSON
dictionaries.

**Tech Stack:** Python standard library, `dataclasses`, `enum`, `unittest`,
existing ORRO shell-reference flow.

---

### Task 1: Lock the claim invariant

**Files:**
- Create: `witnessd/claim.py`
- Create: `tests/test_claim.py`

- [ ] Write tests that call `Claim.from_producer` with an `evaluation`
  argument and expect `TypeError`.
- [ ] Write tests that directly pair `source=producer` with `evaluation=pass`
  and expect `ValueError`.
- [ ] Write tests for verifier construction and every finite vocabulary.
- [ ] Run `/usr/bin/python3 -m unittest tests.test_claim` and confirm the new
  tests fail because `witnessd.claim` is absent.
- [ ] Implement the enums, immutable claim, invariant, and asymmetric
  factories.
- [ ] Re-run `/usr/bin/python3 -m unittest tests.test_claim`.

### Task 2: Project the four producer declarations

**Files:**
- Modify: `witnessd/model_declaration.py`
- Modify: `witnessd/write_scope_declaration.py`
- Modify: `witnessd/skill_routing_declaration.py`
- Modify: `witnessd/tool_declaration.py`
- Modify: `tests/test_declaration_markers.py`
- Modify: `tests/test_write_scope_declaration.py`

- [ ] Add compatibility tests that assert the exact existing dictionaries for
  representative requested, observed, accepted, rejected, conforming,
  nonconforming, advisory, blocking, observed-tool, and enforced-only cases.
- [ ] Run the focused declaration tests and confirm failures identify the
  missing claim-backed projection API.
- [ ] Build a producer claim in each declaration builder and render the same
  legacy dictionary from it.
- [ ] Re-run the focused declaration and adapter tests.

### Task 3: Convert the operator report projection

**Files:**
- Modify: `witnessd/orro_report.py`
- Modify: `tests/test_declaration_markers.py`
- Modify: `tests/test_orro_report.py`

- [ ] Add a report-summary test covering all four declarations and exact
  compatibility fields.
- [ ] Run the focused report tests and confirm the new expectation fails.
- [ ] Reconstruct producer claims from stored declarations before rendering
  the existing declaration summary dictionaries.
- [ ] Re-run the focused report tests.

### Task 4: Update distribution version

**Files:**
- Modify: `setup.py`
- Modify: `witnessd/distribution.py`
- Modify: `tests/test_distribution.py`

- [ ] Add or update the version assertion for `2.39.0`.
- [ ] Run the focused distribution test and confirm it fails on `2.38.0`.
- [ ] Set both required version sources to `2.39.0`.
- [ ] Re-run the focused distribution test.

### Task 5: Prove behavior and repository health

**Files:**
- No tracked output files.

- [ ] Run the same genuine ORRO flow against a second clone of the seed
  repository and capture JSON from `status`, `handoff`, and `proofcheck`.
- [ ] Normalize only volatile path, hash, signature, key, and timestamp values;
  diff field paths and decision-bearing values with the baseline.
- [ ] Recreate the three-file forged run and capture blocked/unrevalidated
  outputs.
- [ ] Run a producer-construction attempt with a verifier evaluation and
  capture the exception.
- [ ] Run the clean-environment full suite with sibling Depone on `main`.
- [ ] Run compileall, source-only overclaim grep, diff checks, and final git
  status.
