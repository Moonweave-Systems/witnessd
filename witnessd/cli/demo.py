from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path


DEMO_GOAL = "write a guarded sample file"
DEMO_WRITE_SCOPE = "src/**"


def _cmd_orro_demo(args: argparse.Namespace) -> int:
    root = (
        Path(args.work_dir).resolve(strict=False)
        if args.work_dir
        else Path(tempfile.mkdtemp(prefix="orro-guardrail-demo-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "sample-repo"
    home = root / ".witnessd"
    run_dir = root / "run"
    runner = root / "runner"
    depone_root = _resolve_depone_root(args.depone_root)

    try:
        _seed_sample_repo(repo)
        run_dir.mkdir()
        runner.mkdir()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ORRO demo setup blocked: {exc}")
        return 2

    command = (
        "echo guardrail-demo > outside.txt"
        if args.violate
        else "mkdir -p src && echo guardrail-demo > src/generated.txt"
    )
    workflow_plan = run_dir / "workflow-plan.json"
    role_lane_plan = run_dir / "role-lane-plan.json"
    verdict_path = run_dir / "proofcheck-verdict.json"
    phases = [
        [
            "init",
            "--home",
            str(home),
            "--repo",
            str(repo),
            "--depone-root",
            str(depone_root),
        ],
        [
            "flowplan",
            DEMO_GOAL,
            "--root",
            str(repo),
            "--profile",
            "code-change",
            "--out",
            str(workflow_plan),
            "--role-lanes-out",
            str(role_lane_plan),
            "--lane-adapter",
            "shell",
            "--write-scope",
            DEMO_WRITE_SCOPE,
            "--command",
            command,
            "--json",
        ],
        [
            "proofrun",
            DEMO_GOAL,
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--workflow-plan",
            str(workflow_plan),
            "--role-lane-plan",
            str(role_lane_plan),
            "--adapter",
            "shell",
            "--runner-sandbox",
            str(runner),
            "--run-dir",
            str(run_dir),
            "--json",
        ],
    ]
    for argv in phases:
        code, payload, stderr = _invoke(argv)
        if code != 0:
            print(
                "ORRO demo blocked before policy verification: "
                + _phase_error(payload, stderr)
            )
            return code

    proofcheck_code, payload, stderr = _invoke(
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
    if not verdict_path.is_file():
        print(
            "ORRO demo blocked during policy verification: "
            + _phase_error(payload, stderr)
        )
        return proofcheck_code or 2
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    policy = verdict.get("policy_conformance")
    if not isinstance(policy, dict):
        print("ORRO demo blocked: Depone verdict lacks policy_conformance")
        return 2

    print(
        "This demo uses deterministic shell execution standing in for an agent; "
        "the same sealed evidence path applies to a real AI adapter."
    )
    if policy.get("overall") == "pass":
        print(
            "Policy conformance: PASS — touched files ⊆ declared write-scope "
            f"({DEMO_WRITE_SCOPE})"
        )
        return 0

    axis = next(
        (
            item
            for item in policy.get("axes", [])
            if isinstance(item, dict) and item.get("axis") == "write_scope"
        ),
        {},
    )
    path = str(axis.get("evidence_path") or "<unknown>")
    blocks_handoff = "true" if axis.get("blocks_handoff") is True else "false"
    print(
        "Policy conformance: FAIL — write_scope violated: "
        f"{path} outside {DEMO_WRITE_SCOPE}  (blocks_handoff: {blocks_handoff})"
    )
    return 1


def _resolve_depone_root(value: str | None) -> Path:
    candidate = value or os.environ.get("WITNESSD_DEPONE_ROOT")
    if candidate:
        return Path(candidate).expanduser().resolve(strict=False)
    return (Path(__file__).resolve().parents[3] / "depone").resolve(strict=False)


def _seed_sample_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "guardrail@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ORRO Guardrail Demo"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("ORRO guardrail demo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed sample repo"], cwd=repo, check=True)


def _invoke(argv: list[str]) -> tuple[int, object, str]:
    from witnessd.__main__ import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    raw = stdout.getvalue().strip()
    try:
        payload: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = raw
    return code, payload, stderr.getvalue().strip()


def _phase_error(payload: object, stderr: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        return str(payload)
    return stderr or str(payload)
