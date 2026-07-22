from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from witnessd.cli._output import (
    _hash_file,
    _invoke_cli_capture,
    _structured_error,
    _write_json_file,
)


def _emit_blocker(error: dict[str, object]) -> int:
    print(
        json.dumps(
            {
                "kind": "orro-companion-result",
                "decision": "blocked",
                "error": error,
            },
            sort_keys=True,
        )
    )
    return 2


def _invoke_phase(argv: list[str]) -> tuple[int, object, str]:
    try:
        code, stdout, stderr = _invoke_cli_capture(argv)
    except Exception as exc:  # noqa: BLE001 - never leak a phase traceback
        return 1, {}, str(exc)
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return code, payload, stderr.strip()


def _resolve_base(repo: Path, base: str | None) -> str:
    if base:
        return base
    try:
        ref = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "symbolic-ref",
                "--quiet",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        name = ref.stdout.strip().rsplit("/", 1)[-1]
        return name or "main"
    except Exception:  # noqa: BLE001 - fallback is intentionally deterministic
        return "main"


def _assert_no_execution_adapter(role_lane_plan_path: Path) -> None:
    plan = json.loads(role_lane_plan_path.read_text(encoding="utf-8"))
    for lane in plan.get("lanes", []):
        if not isinstance(lane, dict):
            continue
        if str(lane.get("adapter")) != "shell":
            raise RuntimeError(
                "ERR_ORRO_CHECK_EXECUTION_LANE_FORBIDDEN: lane "
                f"{lane.get('lane_id')!r} has non-shell adapter "
                f"{lane.get('adapter')!r}"
            )


def _record_command_lane_health(
    *,
    evidence_dir: Path,
    health_run_dir: Path,
    gates: list[dict[str, object]],
) -> None:
    ledger_path = health_run_dir / "team-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    lanes = ledger.get("lanes") if isinstance(ledger, dict) else None
    if not isinstance(lanes, list) or len(lanes) != 1 or not isinstance(lanes[0], dict):
        raise ValueError("health shell run must seal exactly one lane")
    receipt_ref = lanes[0].get("worktree_receipt")
    if not isinstance(receipt_ref, str) or not receipt_ref:
        raise ValueError("health shell lane lacks a worktree receipt")
    receipt_path = health_run_dir / receipt_ref
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    command_receipts = (
        receipt.get("command_receipts") if isinstance(receipt, dict) else None
    )
    if not isinstance(command_receipts, list) or len(command_receipts) < len(gates):
        raise ValueError("health shell lane lacks per-gate command receipts")

    health_dir = evidence_dir / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    contract_gates: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    for index, gate in enumerate(gates):
        receipt_entry = command_receipts[index]
        if not isinstance(receipt_entry, dict):
            raise ValueError(f"health command receipt {index} must be an object")
        expected_command = ["sh", "-c", str(gate["command"])]
        if receipt_entry.get("command") != expected_command:
            raise ValueError(
                f"health command receipt {index} does not match its declared gate"
            )
        exit_code = receipt_entry.get("exit_code")
        if type(exit_code) is not int:
            raise ValueError(
                f"health command receipt {index} lacks an integer exit code"
            )
        gate_id = str(gate["gate"])
        stem = f"{index:02d}-{gate_id}"
        exit_path = health_dir / f"{stem}.exit"
        log_path = health_dir / f"{stem}.log"
        exit_path.write_text(f"{exit_code}\n", encoding="utf-8")
        log_path.write_text(
            str(receipt_entry.get("stdout", "")) + str(receipt_entry.get("stderr", "")),
            encoding="utf-8",
        )
        contract_gates.append(
            {
                "gate": gate_id,
                "tool": str(gate["tool"]),
                "enforcement": str(gate["enforcement"]),
                "expected_exit_code": 0,
                "exit_code_path": exit_path.relative_to(evidence_dir).as_posix(),
                "log_path": log_path.relative_to(evidence_dir).as_posix(),
            }
        )
        observations.append(
            {
                "gate": gate_id,
                "command_receipt_index": index,
                "receipt": str(receipt_path),
                "exit_code": exit_code,
            }
        )
    _write_json_file(health_dir / "gates.json", {"gates": contract_gates})
    _write_json_file(
        health_dir / "observation.json",
        {
            "kind": "orro-health-command-observation",
            "source_receipt": str(receipt_path),
            "source_receipt_sha256": _hash_file(receipt_path),
            "gates": observations,
        },
    )


def _execution_adapter_lane_count(team_ledger_path: Path) -> int:
    try:
        ledger = json.loads(team_ledger_path.read_text(encoding="utf-8"))
        lanes = ledger.get("lanes", []) if isinstance(ledger, dict) else []
        if not isinstance(lanes, list):
            return 0
        return sum(
            1
            for lane in lanes
            if isinstance(lane, dict)
            and (lane.get("runner_adapter_kind") or lane.get("team_adapter_kind"))
            not in {None, "shell"}
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0


def _print_human_summary(
    manifest: dict[str, object], *, reviewer: str | None = None
) -> None:
    verdict_ref = manifest["verdict_ref"]
    assert isinstance(verdict_ref, dict)
    verdict = verdict_ref["decision"]
    dot = "● pass" if verdict == "pass" else f"● {verdict}"
    print("orro check — evidence & review for work you already drove\n")
    bootstrap = manifest.get("health_bootstrap")
    if isinstance(bootstrap, dict):
        config = bootstrap.get("config")
        if isinstance(config, dict):
            written = ", ".join(str(item) for item in config.get("written", []))
            present = ", ".join(str(item) for item in config.get("present", []))
            print(f"  BOOTSTRAP   config written: {written or '(none)'}")
            print(f"              already present: {present or '(none)'}")
        if "profile" in bootstrap:
            print(f"              .orro/health.json: {bootstrap['profile']}")
        promoted = bootstrap.get("promoted")
        if isinstance(promoted, list) and promoted:
            print(f"              promoted to block: {', '.join(map(str, promoted))}")
        if config is not None:
            print(
                "              config only; use --fix --write-scope '<glob>' "
                "--apply for the one-time reformat"
            )
        print()
    declared_intent = manifest.get("declared_intent")
    if isinstance(declared_intent, dict):
        print(f"  DECLARED INTENT   {declared_intent['intent']}")
        non_goals = declared_intent.get("non_goals")
        if isinstance(non_goals, list) and non_goals:
            print(f"    non-goals: {'; '.join(non_goals)}")
        print()
    code_health = manifest.get("code_health")
    if isinstance(code_health, dict):
        health_verdict = str(code_health["verdict"])
        health_dot = "● pass" if health_verdict == "pass" else f"● {health_verdict}"
        print(f"  CODE HEALTH   (Depone verdict, deterministic gates)   {health_dot}")
        gates = code_health.get("gates")
        if isinstance(gates, list):
            for gate in gates:
                if not isinstance(gate, dict):
                    continue
                status = str(gate.get("status", "blocked"))
                enforcement = str(gate.get("enforcement", "block"))
                marker = (
                    "✓"
                    if status == "pass"
                    else "⚠" if enforcement == "advisory" else "✗"
                )
                print(
                    f"    {marker} {str(gate.get('gate', '')):<8} "
                    f"{str(gate.get('tool', '')):<10} "
                    f"{str(gate.get('version', 'unresolved')):<12} {status} "
                    f"({enforcement})"
                )
        fixes = code_health.get("fixes_applied")
        if isinstance(fixes, dict):
            ran = fixes.get("ran")
            diff_ref = fixes.get("diff_ref")
            commands = ", ".join(str(command) for command in ran or [])
            diff_path = (
                Path(str(diff_ref.get("path"))).name
                if isinstance(diff_ref, dict)
                else "health-fix.diff"
            )
            applied_note = (
                " (applied to working tree)"
                if fixes.get("applied_to_worktree") is True
                else ""
            )
            print(
                f"    fixes applied: {commands or '(none)'}   → {diff_path}"
                f"{applied_note}"
            )
    print(f"  VERIFICATION   (Depone verdict, deterministic)   {dot}")
    review_ref = manifest.get("review_ref")
    if isinstance(review_ref, dict):
        print("  REVIEWED   (advisory — not part of verdict)")
        print(f"    → {review_ref['path']}")
    review_skipped = manifest.get("review_skipped")
    if isinstance(review_skipped, dict):
        print(
            f"  ⚠ review skipped: {review_skipped['reason']} "
            f"(install {reviewer or 'the reviewer'}, or pass --no-review)"
        )
    print("  BOUNDARY")
    if isinstance(code_health, dict):
        print(f'    "health: {code_health["verdict"]}" = {code_health["means"]}')
    adapter_count = manifest["execution_adapter_lanes_spawned"]
    print(
        "    reviewed work was NOT observed-executed · "
        f"{adapter_count} execution-adapter lanes · does not approve merge"
    )
    print(f"\n  verdict: {verdict}")


def manifest_partial(
    decision: str, verdict_path: Path, team_ledger_path: Path
) -> dict[str, object]:
    return {
        "kind": "orro-companion-manifest",
        "scope": "state-verified",
        "reviewed_work_execution_observed": False,
        "verification_checks_executed_observed": True,
        "execution_adapter_lanes_spawned": _execution_adapter_lane_count(
            team_ledger_path
        ),
        "verdict_ref": {
            "path": str(verdict_path),
            "sha256": _hash_file(verdict_path),
            "decision": decision,
        },
        "boundary": {
            "reviewed_work_execution_observed": False,
            "depone_verified": False,
            "raises_assurance": False,
            "approves_merge": False,
            "review_is_advisory": True,
        },
    }


def _emit_verdict_with_blocker(
    manifest: dict[str, object], error: dict[str, object]
) -> int:
    print(
        json.dumps(
            {
                "kind": "orro-companion-result",
                "decision": "blocked",
                "verdict_ref": manifest["verdict_ref"],
                "error": error,
            },
            sort_keys=True,
        )
    )
    return 2


def _review_summary_text(path: Path) -> str:
    """Collect only review summary/finding text, excluding the injected goal."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""

    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"summary", "finding", "findings"}:
                    collect(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return "\n".join(parts)


def _review_goal(goal: str, declared_intent: dict[str, Any] | None) -> str:
    if declared_intent is None:
        return goal
    lines = [
        goal,
        "",
        "Declared human intent (verbatim):",
        str(declared_intent["intent"]),
    ]
    non_goals = declared_intent.get("non_goals")
    if isinstance(non_goals, list) and non_goals:
        lines.extend(
            ["Declared non-goals (verbatim):", *[f"- {item}" for item in non_goals]]
        )
    return "\n".join(lines)


def _cmd_orro_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve(strict=False) if args.repo else Path.cwd()
    if args.apply and not args.fix:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_HEALTH_APPLY_REQUIRES_FIX",
                message="orro check --apply requires --fix",
                reason="apply needs a fix lane to produce a verified diff",
                required_input_or_grant="add --fix and --write-scope",
                next_command=(
                    "python3 -m orro check --health --fix "
                    "--write-scope '<glob>' --apply --repo <repo>"
                ),
            )
        )
    bootstrap_report: dict[str, object] | None = None
    if args.init:
        from witnessd.health_detect import (
            ensure_health_profile,
            seed_missing_gate_config,
        )

        try:
            config_report = seed_missing_gate_config(repo)
            _, profile_written = ensure_health_profile(repo)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_INIT_BLOCKED",
                    message="orro check --init could not seed the health profile",
                    reason=str(exc),
                    required_input_or_grant="writable UTF-8 pyproject.toml and .orro",
                    next_command="python3 -m orro check --health --init --repo <repo>",
                )
            )
        bootstrap_report = {
            "config": config_report,
            "profile": "written" if profile_written else "present",
        }
    if args.promote:
        from witnessd.health_detect import promote_health_gates

        try:
            promote_health_gates(repo, args.promote)
        except FileNotFoundError:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_NO_PROFILE",
                    message="orro check --promote requires .orro/health.json",
                    reason="run --init first",
                    required_input_or_grant="a persisted health profile",
                    next_command="python3 -m orro check --health --init --repo <repo>",
                )
            )
        except ValueError as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_UNKNOWN_GATE",
                    message="orro check --promote named an unknown health gate",
                    reason=str(exc),
                    required_input_or_grant="a gate named in .orro/health.json",
                    next_command=("python3 -m orro check --health-plan --repo <repo>"),
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_PROFILE_INVALID",
                    message="orro check could not update .orro/health.json",
                    reason=str(exc),
                )
            )
        if bootstrap_report is None:
            bootstrap_report = {}
        bootstrap_report["promoted"] = list(dict.fromkeys(args.promote))

    health_mode = bool(args.health or args.init or args.promote)
    health_requested = bool(health_mode or args.health_plan or args.fix)
    health_gates: list[dict[str, object]] = []
    if health_requested:
        from witnessd.health_detect import detect_health_gates

        try:
            health_gates = list(detect_health_gates(repo))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_PROFILE_INVALID",
                    message="orro check could not read .orro/health.json",
                    reason=str(exc),
                )
            )
    if args.health_plan:
        payload: dict[str, object] = {
            "kind": "orro-health-plan",
            "gates": health_gates,
        }
        if bootstrap_report is not None:
            payload["bootstrap"] = bootstrap_report
        print(json.dumps(payload, sort_keys=True))
        return 0

    checks = list(getattr(args, "check", None) or [])
    if health_mode and not health_gates:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_HEALTH_NO_GATES_DETECTED",
                message="orro check --health detected no configured health gates",
                reason=(
                    "health gates are read from the repo's own tool config and "
                    "none was found"
                ),
                required_input_or_grant=(
                    "add tool config (e.g. [tool.ruff]) or pass --check '<cmd>'"
                ),
                next_command=(
                    "python3 -m orro check --health --health-plan --repo <repo>"
                ),
            )
        )
    if health_mode and not checks:
        checks.append("true")
    if args.fix and not [scope for scope in args.write_scope if scope]:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_HEALTH_FIX_SCOPE_REQUIRED",
                message="orro check --fix requires an explicit write scope",
                reason="the fixer write scope is never inferred",
                required_input_or_grant="--write-scope '<glob>' (repeatable)",
                next_command=(
                    "python3 -m orro check --health --fix "
                    "--write-scope '<glob>' --repo <repo>"
                ),
            )
        )
    if not checks:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_NO_CHECKS_DECLARED",
                message="orro check requires at least one --check command",
                reason="checks define what 'verified' means and cannot be inferred",
                required_input_or_grant="--check '<cmd>' (repeatable)",
                next_command="python3 -m orro check --check '<cmd>' --repo <repo>",
            )
        )

    declared_intent = None
    if args.intent:
        from witnessd.orro_advisory import OrroAdvisoryError
        from witnessd.orro_intent import read_declared_intent

        try:
            declared_intent = read_declared_intent(Path(args.intent))
        except OrroAdvisoryError as exc:
            return _emit_blocker(_structured_error(code=exc.code, message=str(exc)))

    home = Path(args.home).resolve(strict=False) if args.home else repo / ".witnessd"
    run_dir = (
        Path(args.run_dir).resolve(strict=False)
        if args.run_dir
        else home / "companion-run"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    intent_reference = None
    if declared_intent is not None:
        from witnessd.orro_intent import declared_intent_ref

        intent_path = run_dir / "declared-intent.json"
        try:
            _write_json_file(intent_path, declared_intent)
            intent_reference = declared_intent_ref(intent_path)
        except OSError as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_INTENT_READ_FAILED",
                    message=f"cannot write declared intent sidecar: {exc}",
                )
            )
    sandbox = run_dir / "sandbox"
    base = _resolve_base(repo, args.base)
    goal = f"Review the changes on HEAD relative to {base} without editing files"
    review_goal = _review_goal(goal, declared_intent)

    code, _, err = _invoke_phase(["init", "--home", str(home), "--repo", str(repo)])
    if code != 0:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_INIT_BLOCKED",
                message="companion could not provision home",
                reason=err or "init returned nonzero",
                required_input_or_grant=(
                    "ensure the pinned Depone is provisionable (see orro init)"
                ),
                next_command="python3 -m orro init --home <home> --repo <repo>",
            )
        )

    fix_commands: list[str] = []
    fix_diff_ref: dict[str, str] | None = None
    applied_to_worktree = False
    verify_repo = repo
    if args.fix:
        from witnessd.health_detect import safe_fixer_commands

        fix_commands = safe_fixer_commands(health_gates)
        if fix_commands:
            fix_run_dir = run_dir / "health-fix-run"
            fix_run_dir.mkdir(parents=True, exist_ok=True)
            fix_wp = fix_run_dir / "workflow-plan.json"
            fix_rlp = fix_run_dir / "role-lane-plan.json"
            fix_verdict_path = fix_run_dir / "proofcheck-verdict.json"
            fix_goal = (
                "Apply configured code-health safe fixers within declared write scope"
            )
            fix_flowplan_argv = [
                "flowplan",
                fix_goal,
                "--root",
                str(repo),
                "--profile",
                "code-change",
                "--out",
                str(fix_wp),
                "--role-lanes-out",
                str(fix_rlp),
                "--lane-adapter",
                "shell",
            ]
            for scope in args.write_scope:
                fix_flowplan_argv.extend(["--write-scope", scope])
            for command in fix_commands:
                fix_flowplan_argv.extend(["--command", command])
            fix_flowplan_argv.append("--json")
            code, _, err = _invoke_phase(fix_flowplan_argv)
            if code != 0:
                return _emit_blocker(
                    _structured_error(
                        code="ERR_ORRO_HEALTH_FIX_FLOWPLAN_BLOCKED",
                        message="health fixer flowplan failed",
                        reason=err or "flowplan returned nonzero",
                        required_input_or_grant=(
                            "resolve the reported scope-bounded fixer plan blocker"
                        ),
                        next_command=(
                            "python3 -m orro flowplan ... --profile code-change"
                        ),
                    )
                )
            _assert_no_execution_adapter(fix_rlp)
            _, _, fix_proofrun_err = _invoke_phase(
                [
                    "proofrun",
                    fix_goal,
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                    "--workflow-plan",
                    str(fix_wp),
                    "--role-lane-plan",
                    str(fix_rlp),
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    str(run_dir / "health-fix-sandbox"),
                    "--run-dir",
                    str(fix_run_dir),
                    *(
                        ["--roadmap-item", args.roadmap_item]
                        if args.roadmap_item
                        else []
                    ),
                    "--json",
                ]
            )
            fix_team_ledger = fix_run_dir / "team-ledger.json"
            if not fix_team_ledger.is_file():
                return _emit_blocker(
                    _structured_error(
                        code="ERR_ORRO_HEALTH_FIX_PROOFRUN_BLOCKED",
                        message="health fixer proofrun sealed no evidence",
                        reason=(
                            fix_proofrun_err
                            or "proofrun returned nonzero without sealing team-ledger.json"
                        ),
                        required_input_or_grant=(
                            "resolve the reported scope-bounded fixer blocker"
                        ),
                        next_command="python3 -m orro proofrun ...",
                    )
                )
            _, fix_verdict_payload, fix_verdict_err = _invoke_phase(
                [
                    "proofcheck",
                    "--evidence-dir",
                    str(fix_run_dir),
                    "--home",
                    str(home),
                    "--out",
                    str(fix_verdict_path),
                    "--json",
                ]
            )
            try:
                fix_verdict = json.loads(fix_verdict_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                fix_verdict = {}
            fix_decision = (
                fix_verdict_payload.get("decision")
                if isinstance(fix_verdict_payload, dict)
                else None
            )
            policy = (
                fix_verdict.get("policy_conformance")
                if isinstance(fix_verdict, dict)
                else None
            )
            if (
                fix_decision != "pass"
                or not isinstance(policy, dict)
                or policy.get("overall") != "pass"
            ):
                return _emit_blocker(
                    _structured_error(
                        code="ERR_ORRO_HEALTH_FIX_PROOFCHECK_BLOCKED",
                        message="Depone did not confirm the health fixer write scope",
                        reason=(
                            fix_verdict_err
                            or f"decision={fix_decision!r}, policy_conformance={policy!r}"
                        ),
                        required_input_or_grant=(
                            "keep fixer mutations inside every declared --write-scope"
                        ),
                        next_command="python3 -m orro proofcheck ...",
                    )
                )

            try:
                fix_ledger = json.loads(fix_team_ledger.read_text(encoding="utf-8"))
                fix_lanes = fix_ledger["lanes"]
                fix_receipt_rel = fix_lanes[0]["worktree_receipt"]
                fix_receipt = json.loads(
                    (fix_run_dir / fix_receipt_rel).read_text(encoding="utf-8")
                )
                verify_repo = Path(fix_receipt["worktree"]).resolve(strict=True)
                fix_base_commit = str(fix_receipt["base_commit"])
                fix_head_commit = str(fix_receipt["head_commit"])
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                KeyError,
                IndexError,
                TypeError,
            ) as exc:
                return _emit_blocker(
                    _structured_error(
                        code="ERR_ORRO_HEALTH_FIX_RECEIPT_BLOCKED",
                        message="could not resolve the proofchecked fixer worktree",
                        reason=str(exc),
                        required_input_or_grant=(
                            "a valid code-change worktree lane receipt"
                        ),
                        next_command="python3 -m orro proofcheck ...",
                    )
                )

        diff_path = run_dir / "health-fix.diff"
        if fix_commands:
            diff_argv = [
                "git",
                "-C",
                str(verify_repo),
                "diff",
                "--binary",
                "--no-ext-diff",
            ]
            diff_argv.extend([fix_base_commit, fix_head_commit, "--"])
            diff_result = subprocess.run(diff_argv, capture_output=True, check=False)
            if diff_result.returncode != 0:
                return _emit_blocker(
                    _structured_error(
                        code="ERR_ORRO_HEALTH_FIX_DIFF_BLOCKED",
                        message="could not capture the post-fixer repository diff",
                        reason=diff_result.stderr.decode(
                            "utf-8", errors="replace"
                        ).strip(),
                        required_input_or_grant="a readable Git worktree",
                        next_command=(
                            f"git -C {verify_repo} diff --binary --no-ext-diff"
                        ),
                    )
                )
            diff_bytes = diff_result.stdout
        else:
            diff_bytes = b""
        try:
            diff_path.write_bytes(diff_bytes)
        except OSError as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_FIX_DIFF_BLOCKED",
                    message="could not write health-fix.diff",
                    reason=str(exc),
                )
            )
        fix_diff_ref = {"path": str(diff_path), "sha256": _hash_file(diff_path)}
        if args.apply:
            verified_diff_path = Path(fix_diff_ref["path"])
            if verified_diff_path.is_file() and verified_diff_path.stat().st_size > 0:
                apply_result = subprocess.run(
                    ["git", "-C", str(repo), "apply", str(verified_diff_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if apply_result.returncode != 0:
                    return _emit_blocker(
                        _structured_error(
                            code="ERR_ORRO_HEALTH_APPLY_FAILED",
                            message="could not apply the verified health fixer diff",
                            reason=apply_result.stderr.strip(),
                            required_input_or_grant=(
                                "a caller working tree that accepts health-fix.diff"
                            ),
                            next_command=f"git -C {repo} apply {verified_diff_path}",
                        )
                    )
                applied_to_worktree = True

    if health_mode:
        health_run_dir = run_dir / "health-run"
        health_run_dir.mkdir(parents=True, exist_ok=True)
        health_wp = health_run_dir / "workflow-plan.json"
        health_rlp = health_run_dir / "role-lane-plan.json"
        health_goal = "Run configured code-health gates under observation"
        health_flowplan_argv = [
            "flowplan",
            health_goal,
            "--root",
            str(verify_repo),
            "--profile",
            "verification-only",
            "--out",
            str(health_wp),
            "--role-lanes-out",
            str(health_rlp),
            "--lane-adapter",
            "shell",
        ]
        for gate in health_gates:
            health_flowplan_argv.extend(["--check", str(gate["command"])])
        health_flowplan_argv.append("--json")
        code, _, err = _invoke_phase(health_flowplan_argv)
        if code != 0:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_FLOWPLAN_BLOCKED",
                    message="health verification flowplan failed",
                    reason=err or "flowplan returned nonzero",
                )
            )
        _assert_no_execution_adapter(health_rlp)
        _, _, health_proofrun_err = _invoke_phase(
            [
                "proofrun",
                health_goal,
                "--repo",
                str(verify_repo),
                "--home",
                str(home),
                "--workflow-plan",
                str(health_wp),
                "--role-lane-plan",
                str(health_rlp),
                "--adapter",
                "shell",
                "--runner-sandbox",
                str(run_dir / "health-sandbox"),
                "--run-dir",
                str(health_run_dir),
                *(
                    ["--roadmap-item", args.roadmap_item]
                    if args.roadmap_item
                    else []
                ),
                "--json",
            ]
        )
        if not (health_run_dir / "team-ledger.json").is_file():
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_PROOFRUN_BLOCKED",
                    message="health proofrun sealed no evidence",
                    reason=health_proofrun_err or "proofrun returned no team ledger",
                )
            )
        try:
            _record_command_lane_health(
                evidence_dir=run_dir,
                health_run_dir=health_run_dir,
                gates=health_gates,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_OBSERVATION_BLOCKED",
                    message="could not bind health gates to observed command receipts",
                    reason=str(exc),
                )
            )

    verify_wp = run_dir / "verify-workflow-plan.json"
    verify_rlp = run_dir / "verify-role-lane-plan.json"
    verdict_path = run_dir / "proofcheck-verdict.json"
    flowplan_argv = [
        "flowplan",
        goal,
        "--root",
        str(verify_repo),
        "--profile",
        "verification-only",
        "--out",
        str(verify_wp),
        "--role-lanes-out",
        str(verify_rlp),
        "--lane-adapter",
        "shell",
    ]
    for check in checks:
        flowplan_argv.extend(["--check", check])
    flowplan_argv.append("--json")
    code, _, err = _invoke_phase(flowplan_argv)
    if code != 0:
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_FLOWPLAN_BLOCKED",
                message="verification flowplan failed",
                reason=err or "flowplan returned nonzero",
                required_input_or_grant="resolve the reported flowplan blocker",
                next_command=(
                    "python3 -m orro flowplan ... --profile verification-only"
                ),
            )
        )

    _assert_no_execution_adapter(verify_rlp)

    team_ledger = run_dir / "team-ledger.json"
    _, _, proofrun_err = _invoke_phase(
        [
            "proofrun",
            goal,
            "--repo",
            str(verify_repo),
            "--home",
            str(home),
            "--workflow-plan",
            str(verify_wp),
            "--role-lane-plan",
            str(verify_rlp),
            "--adapter",
            "shell",
            "--runner-sandbox",
            str(sandbox),
            "--run-dir",
            str(run_dir),
            *(
                ["--roadmap-item", args.roadmap_item]
                if args.roadmap_item
                else []
            ),
            "--json",
        ]
    )
    if not team_ledger.is_file():
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_PROOFRUN_BLOCKED",
                message="verification proofrun sealed no evidence",
                reason=(
                    proofrun_err
                    or "proofrun returned nonzero without sealing team-ledger.json"
                ),
                required_input_or_grant="resolve the reported proofrun blocker",
                next_command="python3 -m orro proofrun ...",
            )
        )

    _, verdict_payload, verdict_err = _invoke_phase(
        [
            "proofcheck",
            "--evidence-dir",
            str(run_dir),
            "--home",
            str(home),
            "--out",
            str(verdict_path),
            "--json",
        ]
    )
    decision = (
        verdict_payload.get("decision") if isinstance(verdict_payload, dict) else None
    )
    if (
        decision not in {"pass", "fail", "blocked", "blocked-explicit"}
        or not verdict_path.is_file()
    ):
        return _emit_blocker(
            _structured_error(
                code="ERR_ORRO_CHECK_PROOFCHECK_BLOCKED",
                message="Depone produced no usable verdict",
                reason=(
                    verdict_err
                    or f"proofcheck returned an unusable decision: {decision!r}"
                ),
                required_input_or_grant=(
                    "resolve the reported Depone/proofcheck blocker"
                ),
                next_command="python3 -m orro proofcheck ...",
            )
        )

    review_ref = None
    review_skipped = None
    if not args.no_review:
        reviewer = args.reviewer
        reviewer_binary = args.reviewer_binary or reviewer
        resolved = (
            reviewer_binary
            if Path(reviewer_binary).exists()
            else shutil.which(reviewer_binary)
        )
        if not resolved:
            review_skipped = {
                "reason": f"reviewer '{reviewer}' binary not found: {reviewer_binary}",
                "code": "ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
            }
        else:
            review_wp = run_dir / "review-workflow-plan.json"
            review_rlp = run_dir / "review-role-lane-plan.json"
            code, _, err = _invoke_phase(
                [
                    "flowplan",
                    review_goal,
                    "--root",
                    str(repo),
                    "--profile",
                    "review-only",
                    "--out",
                    str(review_wp),
                    "--role-lanes-out",
                    str(review_rlp),
                    "--lane-adapter",
                    reviewer,
                    "--model-policy",
                    "default",
                    "--json",
                ]
            )
            if code != 0:
                review_skipped = {
                    "reason": err or "review flowplan returned nonzero",
                    "code": "ERR_ORRO_CHECK_REVIEW_PLAN_BLOCKED",
                }
            else:
                rc, _, review_err = _invoke_phase(
                    [
                        "orro-review",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--role-lane-plan",
                        str(review_rlp),
                        "--run-dir",
                        str(run_dir),
                        f"--{reviewer}-binary",
                        reviewer_binary,
                        "--json",
                    ]
                )
                review_summary = run_dir / "orro-review-summary.json"
                if rc != 0 or not review_summary.is_file():
                    review_skipped = {
                        "reason": (
                            review_err
                            or "review adapter returned nonzero or produced no summary"
                        ),
                        "code": "ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
                    }
                else:
                    review_ref = {
                        "path": str(review_summary),
                        "sha256": _hash_file(review_summary),
                        "advisory": True,
                    }

    manifest = manifest_partial(decision, verdict_path, team_ledger)
    if health_mode:
        means = (
            "declared deterministic gates ran under observation; the verdict "
            "reflects their exit status, and is NOT a claim of good design, "
            "correct behavior, or structural consistency"
        )
        health_conformance = (
            verdict_payload.get("health_conformance")
            if isinstance(verdict_payload, dict)
            else None
        )
        if not isinstance(health_conformance, dict):
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_CONFORMANCE_MISSING",
                    message="proofcheck did not return health_conformance",
                )
            )
        axes = health_conformance.get("axes")
        if not isinstance(axes, list):
            return _emit_blocker(
                _structured_error(
                    code="ERR_ORRO_HEALTH_CONFORMANCE_INVALID",
                    message="proofcheck returned invalid health_conformance axes",
                )
            )
        surfaced_gates: list[dict[str, object]] = []
        for axis in axes:
            if not isinstance(axis, dict):
                continue
            detected = next(
                (
                    gate
                    for gate in health_gates
                    if gate.get("gate") == axis.get("gate")
                    and gate.get("tool") == axis.get("tool")
                ),
                {},
            )
            surfaced_gates.append(
                {
                    "gate": axis.get("gate"),
                    "tool": axis.get("tool"),
                    "status": axis.get("status"),
                    "enforcement": axis.get("enforcement"),
                    "blocks_handoff": axis.get("blocks_handoff"),
                    "version": detected.get("version", "unresolved"),
                }
            )
        code_health: dict[str, object] = {
            "applied": True,
            "verdict": decision,
            "overall": health_conformance.get("overall"),
            "gates": surfaced_gates,
            "means": means,
            "verdict_source": "depone-verification-only",
            "structural_consistency_covered": False,
        }
        if args.fix:
            assert fix_diff_ref is not None
            code_health["fixes_applied"] = {
                "ran": fix_commands,
                "diff_ref": fix_diff_ref,
                "applied_to_worktree": applied_to_worktree,
            }
        manifest["code_health"] = code_health
    if declared_intent is not None and intent_reference is not None:
        manifest["declared_intent"] = declared_intent
        manifest["declared_intent_ref"] = intent_reference
    if review_ref is not None:
        manifest["scope"] = "state-verified-and-reviewed"
        manifest["review_ref"] = review_ref
        if declared_intent is not None:
            from witnessd.orro_intent import INTENT_ALIGNMENT_NOTE, screen_intent_drift

            manifest["intent_drift_advisory"] = screen_intent_drift(
                _review_summary_text(Path(str(review_ref["path"]))),
                declared_intent.get("non_goals", []),
            )
            manifest["intent_alignment_note"] = INTENT_ALIGNMENT_NOTE
    if review_skipped is not None:
        manifest["review_skipped"] = review_skipped
    if bootstrap_report is not None:
        manifest["health_bootstrap"] = bootstrap_report
    manifest_path = run_dir / "companion-manifest.json"
    _write_json_file(manifest_path, manifest)

    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        _print_human_summary(manifest, reviewer=args.reviewer)
    return 0 if decision == "pass" else 2
