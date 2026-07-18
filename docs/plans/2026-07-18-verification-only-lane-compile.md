# Verification-Only Lane Compilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Design authority: `docs/plans/2026-07-18-verification-only-lane-compile-design.md`. Line anchors below are against main `eaa4f4e`.

**Goal:** Compile the `verification-only` profile into one real, executed witnessd shell lane that runs declared checks with zero granted write scope, satisfying both existing falsifiability gates with no Depone change.

**Architecture:** Add a `check-runner` proofrun role to the profile, a `_verify_lane_from_role` factory keyed on explicit `--check` commands, thread checks through the team-spec builder as `["sh", "-c", check]`, and admit claimless lanes into `run_team` execution only when the spec declares `lane_intent="verification-only"`. All failure/mutation handling reuses existing paths (`ERR_TEAM_LANE_FAILED`, Depone `ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`).

**Tech Stack:** Python stdlib only, `unittest`, existing witnessd machinery. Tests run with the sibling depone: `PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest ...` (full-suite baseline on main: 806 OK).

**Test command shorthand used below:**
```bash
RUN='env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest'
```

---

### Task 1: Make the profile executable (flow, engine call, check-runner role)

**Files:**
- Modify: `witnessd/orro_workflow.py:1366-1391` (profile spec)
- Modify: `tests/test_orro_workflow.py:138-172` (two pinned tests flip)

- [x] **Step 1: Update the two pinned tests to the new contract** (they must FAIL against current code)

Replace `test_verification_only_profile_delegates_verification_without_execution` (`tests/test_orro_workflow.py:138-154`):

```python
    def test_verification_only_profile_compiles_declared_check_execution(
        self,
    ) -> None:
        code, payload = self._flowplan(
            ["verify this evidence", "--root", ".", "--profile", "verification-only"]
        )

        self.assertEqual(code, 0)
        plan = payload["workflow_plan"]
        self.assertEqual(plan["profile"], "verification-only")
        proofrun = next(
            call for call in plan["engine_calls"] if call["phase"] == "proofrun"
        )
        self.assertEqual(proofrun["engine"], "witnessd")
        self.assertTrue(proofrun["executes"])
        self.assertFalse(proofrun["verifies"])
        proofcheck = next(
            call for call in plan["engine_calls"] if call["phase"] == "proofcheck"
        )
        self.assertEqual(proofcheck["engine"], "Depone")
        self.assertFalse(proofcheck["executes"])
        self.assertTrue(proofcheck["verifies"])
        runner = next(
            role for role in plan["roles"] if role["role_id"] == "check-runner"
        )
        self.assertTrue(runner["may_execute"])
        self.assertFalse(runner["may_verify"])
        self.assertEqual(runner["lane_intent"], "verification-only")
```

In `test_workflow_phase_gate_allows_only_declared_execution_phase` (`tests/test_orro_workflow.py:156-172`), replace the verification-only `assertRaises` block (`:167-172`) with:

```python
        verification_only = compile_workflow_plan(
            goal="verify evidence", profile="verification-only"
        )
        assert_workflow_phase_allowed(verification_only, "proofrun")
```

- [x] **Step 2: Run to verify both fail**

Run: `$RUN tests.test_orro_workflow -k verification -k phase_gate -v` (or run the two tests by full name)
Expected: FAIL — no proofrun engine call / phase gate raises.

- [x] **Step 3: Implement the profile change**

Replace the `"verification-only"` spec (`witnessd/orro_workflow.py:1366-1391`) with:

```python
        "verification-only": {
            "roles": [
                _role(
                    "check-runner",
                    "run declared verification checks under observation "
                    "without a write region",
                    "witnessd",
                    "proofrun",
                    may_execute=True,
                    lane_intent="verification-only",
                ),
                _role(
                    "verifier",
                    "verify existing persisted evidence bytes",
                    "Depone",
                    "proofcheck",
                    may_verify=True,
                ),
                _role(
                    "handoff",
                    "package verifier decision references",
                    "ORRO/witnessd",
                    "handoff",
                ),
            ],
            "flow": ["proofrun", "proofcheck", "handoff"],
            "engine_calls": [
                _call("proofrun", "orro proofrun", "witnessd", executes=True),
                _call("proofcheck", "orro proofcheck", "Depone", verifies=True),
                _call("handoff", "orro handoff", "ORRO"),
            ],
            "required_gates": [
                "verification-only lane runs declared checks with an empty write region",
                "verification-only lane mutation is falsified by Depone",
                "proofcheck writes proofcheck-verdict.json",
                "handoff requires passing bound proofcheck verdict",
            ],
        },
```

- [x] **Step 4: Run the module's tests**

Run: `$RUN tests.test_orro_workflow`
Expected: the two updated tests PASS. `test_flowplan_role_lanes_profiles_block_non_execution_profiles` (`:410-429`) may now fail for verification-only — that is Task 2's contract; if it fails here, proceed to Task 2 before committing, then commit Tasks 1+2 separately per file grouping below.

- [x] **Step 5: Commit** (if Step 4 is fully green; otherwise commit at the end of Task 2)

```bash
git add witnessd/orro_workflow.py tests/test_orro_workflow.py
git commit -m "feat(orro): verification-only profile declares executable check-runner proofrun"
```

### Task 2: Compile the lane (`check_commands`, factory, validation, intent guard)

**Files:**
- Modify: `witnessd/orro_workflow.py` (constants `:21-39`, `compile_workflow_plan:98-125`, `compile_role_lane_plan:234-293`, new `_verify_lane_from_role` beside `:910`, `_validate_role_lane:1097-1165`)
- Modify: `tests/test_orro_workflow.py:410-429`
- Test: `tests/test_orro_workflow.py` (new cases in the existing class)

- [x] **Step 1: Write failing tests**

Replace `test_flowplan_role_lanes_profiles_block_non_execution_profiles` (`tests/test_orro_workflow.py:410-429`) — release-readiness keeps the old pin, verification-only gets the new contract:

```python
    def test_flowplan_role_lanes_release_readiness_stays_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, _payload = self._flowplan(
                [
                    "review safely",
                    "--root",
                    tmp,
                    "--profile",
                    "release-readiness",
                    "--role-lanes-out",
                    str(out),
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(role_lanes["execution_allowed"])
            self.assertEqual(role_lanes["lanes"], [])

    def test_verification_only_role_lanes_compile_claimless_check_lane(self) -> None:
        plan = compile_workflow_plan(goal="run checks", profile="verification-only")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=plan,
            check_commands=["/usr/bin/true", "echo observed"],
        )

        self.assertTrue(role_lane_plan["execution_allowed"])
        self.assertEqual(len(role_lane_plan["lanes"]), 1)
        lane = role_lane_plan["lanes"][0]
        self.assertEqual(lane["role_id"], "check-runner")
        self.assertEqual(lane["adapter"], "shell")
        self.assertEqual(lane["region"], [])
        self.assertEqual(lane["lane_intent"], "verification-only")
        self.assertEqual(lane["check_commands"], ["/usr/bin/true", "echo observed"])
        self.assertTrue(lane["may_execute"])
        self.assertFalse(lane["may_verify"])
        self.assertFalse(lane["raises_assurance"])
        self.assertFalse(
            lane["prompt"].startswith(ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX)
        )

    def test_verification_only_role_lanes_require_checks(self) -> None:
        plan = compile_workflow_plan(goal="run checks", profile="verification-only")
        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(workflow_plan=plan)
        self.assertEqual(cm.exception.code, "ERR_ORRO_VERIFICATION_CHECK_REQUIRED")

        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(workflow_plan=plan, check_commands=["  "])
        self.assertEqual(cm.exception.code, "ERR_ORRO_VERIFICATION_CHECK_REQUIRED")

    def test_verification_only_role_lanes_reject_ai_adapters(self) -> None:
        plan = compile_workflow_plan(goal="run checks", profile="verification-only")
        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(
                workflow_plan=plan,
                lane_adapter="codex",
                check_commands=["/usr/bin/true"],
            )
        self.assertEqual(cm.exception.code, "ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED")

    def test_check_commands_rejected_outside_verification_only(self) -> None:
        plan = compile_workflow_plan(goal="fix parser", profile="code-change")
        with self.assertRaises(OrroWorkflowError) as cm:
            compile_role_lane_plan(
                workflow_plan=plan, check_commands=["/usr/bin/true"]
            )
        self.assertEqual(cm.exception.code, "ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED")

    def test_verification_only_profile_rejects_implementation_intent(self) -> None:
        with self.assertRaises(OrroWorkflowError) as cm:
            compile_workflow_plan(
                goal="run checks",
                profile="verification-only",
                lane_intent="implementation",
            )
        self.assertEqual(cm.exception.code, "ERR_ORRO_ROLE_LANE_INTENT_INVALID")

    def test_claimless_verification_lane_survives_write_and_load(self) -> None:
        plan = compile_workflow_plan(goal="run checks", profile="verification-only")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=plan, check_commands=["/usr/bin/true"]
        )
        validate_role_lane_plan(role_lane_plan)

        stripped = deepcopy(role_lane_plan)
        del stripped["lanes"][0]["check_commands"]
        with self.assertRaises(OrroWorkflowError) as cm:
            validate_role_lane_plan(stripped)
        self.assertEqual(cm.exception.code, "ERR_ORRO_VERIFICATION_CHECK_REQUIRED")
```

Add any missing imports at the top of the test file (`compile_role_lane_plan`, `validate_role_lane_plan`, `ROLE_LANE_PLACEHOLDER_PROMPT_PREFIX`, `deepcopy` — check what is already imported).

- [x] **Step 2: Run to verify the new tests fail**

Run: `$RUN tests.test_orro_workflow`
Expected: new tests FAIL (unknown kwarg `check_commands`, missing constants).

- [x] **Step 3: Implement in `witnessd/orro_workflow.py`**

Add constants beside `ERR_ORRO_ROLE_LANE_INTENT_INVALID` (`:39`):

```python
ERR_ORRO_VERIFICATION_CHECK_REQUIRED = "ERR_ORRO_VERIFICATION_CHECK_REQUIRED"
ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED = "ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED"
```

In `compile_workflow_plan` (`:98-125`), inside the existing `if lane_intent is not None:` block after the `VALID_LANE_INTENTS` check, add the contradiction guard:

```python
        if lane_intent == "implementation" and profile == "verification-only":
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_INTENT_INVALID,
                "verification-only profile cannot declare implementation lane intent",
            )
```

In `compile_role_lane_plan` (`:234-293`): add `check_commands: list[str] | None = None` to the signature; after `profile = str(...)` (`:248`) insert:

```python
    if check_commands is not None and profile != "verification-only":
        raise OrroWorkflowError(
            ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED,
            "check commands are only supported by the verification-only profile",
        )
    execution_allowed = profile in {"code-change", "docs-change", "verification-only"}
    lanes: list[dict[str, Any]] = []
    if profile == "verification-only":
        if lane_adapter != "shell":
            raise OrroWorkflowError(
                ERR_ORRO_ROLE_LANE_ADAPTER_UNSUPPORTED,
                "verification-only lanes are deterministic shell lanes only",
            )
        checks = _normalized_check_commands(check_commands)
        if not checks:
            raise OrroWorkflowError(
                ERR_ORRO_VERIFICATION_CHECK_REQUIRED,
                "verification-only role lanes require at least one check command",
            )
        for role in workflow_plan["roles"]:
            if (
                isinstance(role, dict)
                and role.get("phase") == "proofrun"
                and role.get("may_execute") is True
            ):
                lanes.append(
                    _verify_lane_from_role(role, workflow_plan, tier, checks)
                )
    elif execution_allowed:
        ...  # existing code-change/docs-change loop, unchanged
```

(The original `execution_allowed = profile in {"code-change", "docs-change"}` line and `if execution_allowed:` become the code above; `review-only`/`critic-only` branches unchanged.)

Add helpers beside `_role_lane_from_role` (`:910`):

```python
def _normalized_check_commands(check_commands: list[str] | None) -> list[str]:
    if check_commands is None:
        return []
    return [check for check in check_commands if isinstance(check, str) and check.strip()]


def _verify_lane_from_role(
    role: dict[str, Any],
    workflow_plan: dict[str, Any],
    tier: str,
    check_commands: list[str],
) -> dict[str, Any]:
    role_id = str(role["role_id"])
    digest = hashlib.sha256(
        f"{workflow_plan['goal']}:verification-only:{role_id}:shell".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "lane_id": f"{role_id}-{digest}",
        "role_id": role_id,
        "role_purpose": role.get("purpose", ""),
        "phase": "proofrun",
        "engine": "witnessd",
        "adapter": "shell",
        "tier": tier,
        "region": [],
        "prompt": (
            "Run declared verification checks under observation: "
            + "; ".join(check_commands)
        ),
        "budget": {"max_tokens": 0, "max_usd": 0.0, "max_depth": 1},
        "may_execute": True,
        "may_verify": False,
        "raises_assurance": False,
        "lane_intent": "verification-only",
        "check_commands": list(check_commands),
    }
```

In `_validate_role_lane` (`:1097-1165`), after the existing `lane_intent` validation block (`:1153-1160`), add the claimless fail-closed rule (keyed on empty region so flag-path code-change lanes with concrete regions are exempt):

```python
    if lane_intent == "verification-only" and not list(lane.get("region") or []):
        checks = lane.get("check_commands")
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(check, str) and check.strip() for check in checks)
        ):
            raise OrroWorkflowError(
                ERR_ORRO_VERIFICATION_CHECK_REQUIRED,
                "claimless verification-only lane requires non-empty check_commands",
            )
```

- [x] **Step 4: Run the module's tests**

Run: `$RUN tests.test_orro_workflow`
Expected: PASS (all, including Task 1's).

- [x] **Step 5: Commit**

```bash
git add witnessd/orro_workflow.py tests/test_orro_workflow.py
git commit -m "feat(orro): compile verification-only profile into claimless declared-check lane"
```

### Task 3: CLI `--check` and `_cmd_plan` threading

**Files:**
- Modify: `witnessd/__main__.py` (`_add_flowplan_args:5306-5335`, `_cmd_plan` role-lanes block `:1015-1021`, arg guard near `:905`)
- Test: `tests/test_orro_workflow.py` (CLI-level cases via existing `self._flowplan` helper)

- [x] **Step 1: Write failing CLI tests**

```python
    def test_flowplan_check_flag_compiles_verification_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, payload = self._flowplan(
                [
                    "run checks",
                    "--root",
                    tmp,
                    "--profile",
                    "verification-only",
                    "--role-lanes-out",
                    str(out),
                    "--check",
                    "/usr/bin/true",
                    "--check",
                    "echo observed",
                ]
            )

            self.assertEqual(code, 0)
            role_lanes = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(role_lanes["execution_allowed"])
            self.assertEqual(
                role_lanes["lanes"][0]["check_commands"],
                ["/usr/bin/true", "echo observed"],
            )

    def test_flowplan_verification_only_role_lanes_require_check_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "role-lane-plan.json"
            code, payload = self._flowplan(
                [
                    "run checks",
                    "--root",
                    tmp,
                    "--profile",
                    "verification-only",
                    "--role-lanes-out",
                    str(out),
                ]
            )

            self.assertEqual(code, 1)
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_VERIFICATION_CHECK_REQUIRED"
            )
            self.assertFalse(out.exists())

    def test_flowplan_check_flag_requires_verification_role_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, payload = self._flowplan(
                [
                    "run checks",
                    "--root",
                    tmp,
                    "--profile",
                    "verification-only",
                    "--check",
                    "/usr/bin/true",
                ]
            )

            self.assertEqual(code, 2)
            self.assertEqual(
                payload["error"]["code"], "ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED"
            )
```

(Adapt the error-payload shape to whatever `self._flowplan` returns for existing error cases in this file — follow the profile-unknown test's assertions.)

- [x] **Step 2: Run to verify they fail**

Run: `$RUN tests.test_orro_workflow -v` (new tests)
Expected: FAIL (`--check` unknown argument).

- [x] **Step 3: Implement in `witnessd/__main__.py`**

In `_add_flowplan_args` (after `--lane-intent`, `:5315`):

```python
    flowplan.add_argument(
        "--check",
        action="append",
        default=None,
        help="declared verification check command for verification-only role "
        "lanes (repeatable; requires --role-lanes-out)",
    )
```

In `_cmd_plan`, after the workflow-plan compile block (`:905-918`), add the misuse guard:

```python
    if getattr(args, "check", None) and not getattr(args, "role_lanes_out", None):
        _emit_orro_error(
            args,
            code="ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED",
            message="--check requires --role-lanes-out with --profile verification-only",
        )
        return 2
```

In the `compile_role_lane_plan` call (`:1015-1021`) add `check_commands=getattr(args, "check", None),`. The existing `except OrroWorkflowError` handler (`:1029-1042`) already emits the structured error and returns 1 — no new handler.

Check `orro/__main__.py` / the `orro-flow` parser only if `flowplan` args are mirrored there (grep `--lane-intent` to see); mirror `--check` wherever `--lane-intent` is mirrored.

- [x] **Step 4: Run tests**

Run: `$RUN tests.test_orro_workflow`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add witnessd/__main__.py tests/test_orro_workflow.py
git commit -m "feat(cli): flowplan --check declares verification-only lane checks"
```

### Task 4: Team-spec builder emits declared checks

**Files:**
- Modify: `witnessd/__main__.py:640-648` (`_role_lane_plan_team_specs` shell branch)
- Test: `tests/test_orro_workflow.py` (spec-thread test, mirroring the existing lane_intent thread test at `:1158-1197`)

- [x] **Step 1: Write the failing test**

```python
    def test_verification_lane_spec_runs_declared_checks_not_default_write(self) -> None:
        plan = compile_workflow_plan(goal="run checks", profile="verification-only")
        role_lane_plan = compile_role_lane_plan(
            workflow_plan=plan, check_commands=["/usr/bin/true", "echo observed"]
        )
        args = argparse.Namespace(
            codex_binary="codex",
            claude_binary="claude",
            agy_binary="agy",
            gemini_binary="gemini",
            opencode_binary="opencode",
        )

        specs = _role_lane_plan_team_specs(role_lane_plan, args)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["region"], [])
        self.assertEqual(specs[0]["lane_intent"], "verification-only")
        self.assertEqual(
            specs[0]["commands"],
            [["sh", "-c", "/usr/bin/true"], ["sh", "-c", "echo observed"]],
        )
```

(Mirror the import/`args` pattern of the existing spec-thread test at `:1158-1197` — reuse its helper if one exists.)

- [x] **Step 2: Run to verify it fails**

Expected: FAIL — commands equal the `_default_team_lane_command` output (`["sh", "-c", "true"]` for empty region), not the declared checks.

- [x] **Step 3: Implement**

In `_role_lane_plan_team_specs` (`:640-648`), replace the shell branch:

```python
        if adapter == "shell":
            checks = lane.get("check_commands")
            if (
                lane.get("lane_intent") == "verification-only"
                and isinstance(checks, list)
                and checks
            ):
                commands = [["sh", "-c", str(check)] for check in checks]
            else:
                commands = [_default_team_lane_command(str(lane["lane_id"]), region)]
            spec = {
                "lane_id": lane["lane_id"],
                "region": region,
                "commands": commands,
            }
            _attach_role_capability_team_fields(spec, lane)
            specs.append(spec)
            continue
```

Note: flag-path lanes (`code-change` + `--lane-intent verification-only`) carry no `check_commands`, so they keep the default command and stay reconciled by Depone's mutation gate — do not change their behavior.

- [x] **Step 4: Run tests, including the pinned flag-path thread test**

Run: `$RUN tests.test_orro_workflow tests.test_orro_flow`
Expected: PASS (flag-path pins at `tests/test_orro_workflow.py:1158-1197` and `tests/test_orro_flow.py` untouched).

- [x] **Step 5: Commit**

```bash
git add witnessd/__main__.py tests/test_orro_workflow.py
git commit -m "feat(team): verification-only shell specs run declared checks"
```

### Task 5: `run_team` admits declared claimless verification lanes

**Files:**
- Modify: `witnessd/fanin.py:136-143`
- Test: `tests/test_team_fanin.py` (new cases; follow that file's existing `run_team` harness — `_seed_repo`-style git setup + `gen_operator_keypair`, see `tests/test_w16_merge_lanes.py:1-60` for the pattern)

- [x] **Step 1: Write failing tests** (adapt setup helpers to the file's existing conventions)

```python
    def test_claimless_verification_lane_executes_and_passes(self) -> None:
        # setup: seeded git repo, keys, out dir per existing run_team tests
        result = run_team(
            [
                {
                    "lane_id": "check-runner-abc",
                    "region": [],
                    "commands": [["sh", "-c", "true"]],
                    "lane_intent": "verification-only",
                }
            ],
            repo_root=str(repo),
            out_dir=str(out_dir),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
        )

        ledger = json.loads((out_dir / "team-ledger.json").read_text())
        lane = next(
            entry for entry in ledger["lanes"]
            if entry["lane_id"] == "check-runner-abc"
        )
        self.assertEqual(lane["verification_state"], "pass")
        self.assertEqual(lane["lane_intent"], "verification-only")
        self.assertEqual(lane["touched_files"], [])

        verdict = build_team_ledger_verdict(ledger, base_dir=out_dir)
        self.assertEqual(verdict["decision"], "pass")

    def test_claimless_verification_lane_mutation_is_falsified_by_depone(self) -> None:
        # same setup; the declared check writes a file
        result = run_team(
            [
                {
                    "lane_id": "check-runner-mut",
                    "region": [],
                    "commands": [["sh", "-c", "echo x > mutated.txt"]],
                    "lane_intent": "verification-only",
                }
            ],
            ...,
        )

        ledger = json.loads((out_dir / "team-ledger.json").read_text())
        verdict = build_team_ledger_verdict(ledger, base_dir=out_dir)
        self.assertEqual(verdict["decision"], "blocked")
        self.assertIn(
            "ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED",
            {error["code"] for error in verdict["errors"]},
        )

    def test_claimless_verification_lane_check_failure_blocks(self) -> None:
        # same setup; the declared check exits non-zero
        result = run_team(
            [
                {
                    "lane_id": "check-runner-fail",
                    "region": [],
                    "commands": [["sh", "-c", "exit 3"]],
                    "lane_intent": "verification-only",
                }
            ],
            ...,
        )

        ledger = json.loads((out_dir / "team-ledger.json").read_text())
        lane = next(
            entry for entry in ledger["lanes"]
            if entry["lane_id"] == "check-runner-fail"
        )
        self.assertEqual(lane["verification_state"], "blocked")
        self.assertEqual(lane["blocked_reason"], "ERR_TEAM_LANE_FAILED")

    def test_claimless_lane_without_verification_intent_still_skipped(self) -> None:
        # regression pin: undeclared claimless lanes keep audit-and-skip
        result = run_team(
            [
                {
                    "lane_id": "silent-claimless",
                    "region": [],
                    "commands": [["sh", "-c", "true"]],
                }
            ],
            ...,
        )

        ledger = json.loads((out_dir / "team-ledger.json").read_text())
        self.assertEqual(
            [entry["lane_id"] for entry in ledger["lanes"]], []
        )
        self.assertIn(
            "read-only-lane-audit",
            [event["event"] for event in result["runlog"]],
        )
        # NB: tests/test_team_fanin.py:268 already pins this runlog event for an
        # intent-less claimless lane — extend/align with it rather than duplicating.
```

The `...` in setup/assertion plumbing means: reuse this test file's existing fixtures verbatim (repo seeding, key generation, runlog reading, `run_team` kwargs) — copy from the nearest existing `run_team` test in the same file, keeping its exact keyword arguments. The lane-spec dicts and assertions above are the normative content.

- [x] **Step 2: Run to verify current behavior fails them**

Run: `$RUN tests.test_team_fanin -v` (new tests)
Expected: first three FAIL (lane skipped → no ledger lane entry); the regression pin PASSES already.

- [x] **Step 3: Implement in `witnessd/fanin.py:136-143`**

```python
            if not allowed_touched_files:
                if spec.get("lane_intent") != "verification-only":
                    append_runlog(
                        log,
                        run_id,
                        "read-only-lane-audit",
                        payload={"lane_id": lane_id, "commands": commands},
                    )
                    continue
                append_runlog(
                    log,
                    run_id,
                    "verification-only-claimless-lane",
                    payload={"lane_id": lane_id, "commands": commands},
                )
```

(The declared verification lane falls through to `runnable.append(...)` with `allowed_touched_files=[]`; keyed on the declared spec intent, never on observed emptiness.)

- [x] **Step 4: Run and fix fallout in the claimless execution path**

Run: `$RUN tests.test_team_fanin`
If `emit_supervised_lane` (`witnessd/emitter.py:479`) or `_run_write_lane` rejects `allowed_touched_files=[]`, make the narrowest change that lets an empty allowed-list lane emit evidence (this is the only genuinely unexplored seam; keep any change inside the claimless path and re-run the full fanin/emitter test modules after).
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add witnessd/fanin.py tests/test_team_fanin.py
git commit -m "feat(fanin): execute declared claimless verification-only lanes"
```

### Task 6: End-to-end CLI proof + public-flow pin update

**Files:**
- Modify: `tests/test_orro_public_flow.py:1588+` (forbidden-profiles loop)
- Test: new e2e in `tests/test_orro_public_flow.py` (reuse `_init_home`/`_flowplan_out` helpers)

- [x] **Step 1: Update the forbidden-profiles pin**

In `test_proofrun_role_lane_plan_forbidden_profiles_fail_before_run_dir` (`:1588`), change the loop to `for profile in ("critic-only", "review-only"):` — verification-only is no longer forbidden and is covered by the new e2e below.

- [x] **Step 2: Write the e2e test** (same class, reusing its helpers)

```python
    def test_verification_only_flow_runs_checks_and_passes_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)
            plan_path = root / "workflow-plan.json"
            role_lane_path = root / "role-lane-plan.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "flowplan",
                        "verify current state",
                        "--root",
                        str(repo),
                        "--profile",
                        "verification-only",
                        "--out",
                        str(plan_path),
                        "--role-lanes-out",
                        str(role_lane_path),
                        "--check",
                        "true",
                    ]
                )
            self.assertEqual(code, 0)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "verify current state",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--workflow-plan",
                        str(plan_path),
                        "--role-lane-plan",
                        str(role_lane_path),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            run_dir = Path(payload["run_dir"])
            ledger = json.loads((run_dir / "team-ledger.json").read_text())
            lane = ledger["lanes"][0]
            self.assertEqual(lane["lane_intent"], "verification-only")
            self.assertEqual(lane["verification_state"], "pass")
            self.assertEqual(lane["touched_files"], [])
            verdict_path = run_dir / "team-ledger-verdict.json"
            self.assertTrue(verdict_path.exists())
            verdict = json.loads(verdict_path.read_text())
            self.assertEqual(verdict["decision"], "pass")
```

Adapt the workflow-plan/role-lane out-path flags and result-payload keys to how sibling tests in this file already invoke `flowplan`/`proofrun` (follow `test_proofrun_role_lane_plan_forbidden_profiles_fail_before_run_dir` and its helpers exactly; e.g. if `_flowplan_out`/`_role_lane_plan_out` helpers exist, extend them with a `checks=None` parameter instead of inlining argv). If proofrun requires extra gates (risky-goal, adapters), mirror how the code-change e2e in this file satisfies them.

- [x] **Step 3: Run**

Run: `$RUN tests.test_orro_public_flow -v`
Expected: PASS (both the updated pin and the new e2e).

- [x] **Step 4: Commit**

```bash
git add tests/test_orro_public_flow.py
git commit -m "test(e2e): verification-only flowplan->proofrun passes Depone end-to-end"
```

### Task 7: Docs invariant flip + full suite

**Files:**
- Modify: `SPEC3.md:253-254`, `CLAUDE.md:160`, `SKILL.md:187`, `docs/README.md:120`

- [x] **Step 1: Revise every statement of the old invariant**

Replace each "review-only, verification-only, and default release-readiness role-lane plans cannot launch proofrun."-form sentence with (adapt surrounding grammar per file):

> `review-only` and default `release-readiness` role-lane plans cannot launch proofrun. `verification-only` role-lane plans compile declared shell check lanes (`flowplan --check`, repeatable) with an empty write region; proofrun executes those checks under observation, a non-zero check exit blocks the lane, and any mutation is falsified by Depone (`ERR_TEAM_LEDGER_VERIFICATION_LANE_MUTATED`).

Also grep the four files (plus `README.md`) for other "verification-only" statements that describe the profile as non-executing and align them.

- [x] **Step 2: Full suite + self-test**

```bash
env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests
env PYTHONPATH=../depone PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m witnessd self-test --all
```

Expected: 0 failures (baseline 806 + new tests), self-test green.

- [x] **Step 3: Commit**

```bash
git add SPEC3.md CLAUDE.md SKILL.md docs/README.md README.md
git commit -m "docs: verification-only role-lane plans now launch declared check proofrun"
```

---

## Post-implementation verification (independent, not the implementer's)

1. Clean-env full suite (shim-free PATH, `PYTHONNOUSERSITE=1`, sibling depone).
2. Mutation tests — each must break at least one test: (a) revert the `fanin.py` claimless-admit branch, (b) drop `check_commands` threading in `_role_lane_plan_team_specs`, (c) remove the `_validate_role_lane` claimless check rule.
3. Live smoke with the installed/real CLI: `flowplan --profile verification-only --role-lanes-out ... --check "/usr/bin/python3 -c pass"` → proofrun → inspect ledger + Depone verdict; then a deliberately mutating check → blocked.
