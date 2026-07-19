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
            and (
                lane.get("runner_adapter_kind")
                or lane.get("team_adapter_kind")
            )
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
    dot = "● pass" if verdict == "pass" else "● blocked"
    print("orro check — evidence & review for work you already drove\n")
    declared_intent = manifest.get("declared_intent")
    if isinstance(declared_intent, dict):
        print(f"  DECLARED INTENT   {declared_intent['intent']}")
        non_goals = declared_intent.get("non_goals")
        if isinstance(non_goals, list) and non_goals:
            print(f"    non-goals: {'; '.join(non_goals)}")
        print()
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
    checks = list(getattr(args, "check", None) or [])
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
            return _emit_blocker(
                _structured_error(code=exc.code, message=str(exc))
            )

    repo = Path(args.repo).resolve(strict=False) if args.repo else Path.cwd()
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

    verify_wp = run_dir / "verify-workflow-plan.json"
    verify_rlp = run_dir / "verify-role-lane-plan.json"
    verdict_path = run_dir / "proofcheck-verdict.json"
    flowplan_argv = [
        "flowplan",
        goal,
        "--root",
        str(repo),
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
            str(repo),
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
        decision not in {"pass", "blocked", "blocked-explicit"}
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
    manifest_path = run_dir / "companion-manifest.json"
    _write_json_file(manifest_path, manifest)

    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        _print_human_summary(manifest, reviewer=args.reviewer)
    return 0 if decision == "pass" else 2
