from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

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


def _print_human_summary(manifest: dict[str, object]) -> None:
    verdict_ref = manifest["verdict_ref"]
    assert isinstance(verdict_ref, dict)
    verdict = verdict_ref["decision"]
    dot = "● pass" if verdict == "pass" else "● blocked"
    print("orro check — evidence & review for work you already drove\n")
    print(f"  VERIFIED   (Depone verdict, deterministic)   {dot}")
    review_ref = manifest.get("review_ref")
    if isinstance(review_ref, dict):
        print("  REVIEWED   (advisory — not part of verdict)")
        print(f"    → {review_ref['path']}")
    print("  BOUNDARY")
    print(
        "    reviewed work was NOT observed-executed · "
        "0 execution-adapter lanes · does not approve merge"
    )
    print(f"\n  verdict: {verdict}")


def manifest_partial(decision: str, verdict_path: Path) -> dict[str, object]:
    return {
        "kind": "orro-companion-manifest",
        "scope": "state-verified",
        "reviewed_work_execution_observed": False,
        "verification_checks_executed_observed": True,
        "execution_adapter_lanes_spawned": 0,
        "verdict_ref": {
            "path": str(verdict_path),
            "sha256": _hash_file(verdict_path),
            "decision": decision,
        },
        "boundary": {
            "reviewed_work_execution_observed": False,
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

    repo = Path(args.repo).resolve(strict=False) if args.repo else Path.cwd()
    home = Path(args.home).resolve(strict=False) if args.home else repo / ".witnessd"
    run_dir = (
        Path(args.run_dir).resolve(strict=False)
        if args.run_dir
        else home / "companion-run"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox = run_dir / "sandbox"
    base = _resolve_base(repo, args.base)
    goal = f"Review the changes on HEAD relative to {base} without editing files"

    code, _, err = _invoke_phase(
        ["init", "--home", str(home), "--repo", str(repo)]
    )
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
        verdict_payload.get("decision")
        if isinstance(verdict_payload, dict)
        else None
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
    if not args.no_review:
        reviewer = args.reviewer
        reviewer_binary = args.reviewer_binary or reviewer
        resolved = (
            reviewer_binary
            if Path(reviewer_binary).exists()
            else shutil.which(reviewer_binary)
        )
        if not resolved:
            return _emit_verdict_with_blocker(
                manifest_partial(decision, verdict_path),
                _structured_error(
                    code="ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
                    message=(
                        f"reviewer '{reviewer}' binary not found: "
                        f"{reviewer_binary}"
                    ),
                    reason=(
                        "review was requested but the reviewer could not be located; "
                        "silently skipping the review would misrepresent the result"
                    ),
                    required_input_or_grant=(
                        f"install/authenticate {reviewer}, or pass --no-review"
                    ),
                    next_command="python3 -m orro check --no-review ...",
                ),
            )
        review_wp = run_dir / "review-workflow-plan.json"
        review_rlp = run_dir / "review-role-lane-plan.json"
        code, _, err = _invoke_phase(
            [
                "flowplan",
                goal,
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
            return _emit_verdict_with_blocker(
                manifest_partial(decision, verdict_path),
                _structured_error(
                    code="ERR_ORRO_CHECK_REVIEW_PLAN_BLOCKED",
                    message="review flowplan failed",
                    reason=err or "flowplan nonzero",
                    required_input_or_grant=(
                        "resolve the reported flowplan blocker"
                    ),
                    next_command="python3 -m orro check --no-review ...",
                ),
            )
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
            return _emit_verdict_with_blocker(
                manifest_partial(decision, verdict_path),
                _structured_error(
                    code="ERR_ORRO_CHECK_REVIEWER_UNAVAILABLE",
                    message=f"reviewer '{reviewer}' could not run",
                    reason=(
                        review_err
                        or "review adapter returned nonzero or produced no summary"
                    ),
                    required_input_or_grant=(
                        f"install/authenticate {reviewer}, or pass --no-review"
                    ),
                    next_command="python3 -m orro check --no-review ...",
                ),
            )
        review_ref = {
            "path": str(review_summary),
            "sha256": _hash_file(review_summary),
            "advisory": True,
        }

    manifest = manifest_partial(decision, verdict_path)
    if review_ref is not None:
        manifest["scope"] = "state-verified-and-reviewed"
        manifest["review_ref"] = review_ref
    manifest_path = run_dir / "companion-manifest.json"
    _write_json_file(manifest_path, manifest)

    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        _print_human_summary(manifest)
    return 0 if decision == "pass" else 2
