"""witnessd CLI — `run` / `status` / `self-test`.

`run` wires the lane pipeline: observer/runner separation check (Task 4) →
shell adapter (Task 5) → observer_capture (Task 6) → Evidence Emitter (Task 11).
Separation is enforced fail-closed BEFORE any capture happens, so a --out/--log
inside the runner sandbox aborts with ERR_OBSERVER_NOT_SEPARATED and writes
nothing.

`status` renders only through the enum-gated render_status (Task 3): witnessd
never self-declares success; a lane is "evidence-pending" until a separate
Depone verification returns a verdict.

`self-test --all` runs each module's `_self_test` and reports `N/N passed`.
"""

from __future__ import annotations

import argparse
import sys

RUNNER_SANDBOX_HELP = (
    "filesystem DIR where the runner executes; NOT a Codex sandbox mode "
    "(read-only/workspace-write) and NOT the observer run/out directory"
)


def _cli_handler(module: str, name: str):
    def _invoke(args: argparse.Namespace) -> int:
        import importlib

        return getattr(importlib.import_module(f"witnessd.cli.{module}"), name)(args)

    return _invoke


DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS = 900


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="witnessd")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="initialize witnessd config and pinned Depone")
    init.add_argument("--home", default=None)
    init.add_argument("--repo", "--root", dest="repo", default=".")
    init.add_argument("--depone-root", default=None)
    init.add_argument("--depone-repository", default=None)
    init.add_argument("--depone-ref", default=None)
    init.add_argument("--team", default=None)
    init.add_argument("--json", action="store_true", help="emit machine-readable output")
    init.add_argument(
        "--allow-network",
        action="store_true",
        help="allow setup-time network provisioning when no local Depone root is supplied",
    )
    init.set_defaults(func=_cli_handler("bootstrap", "_cmd_init"))

    orro_setup = sub.add_parser(
        "orro-setup",
        help=argparse.SUPPRESS,
    )
    orro_setup.add_argument("--home", default=".witnessd", help="witnessd home directory")
    orro_setup.add_argument("--depone-root", default=None, help="use this pinned Depone checkout")
    orro_setup.add_argument("--depone-repository", default=None, help="clone Depone from this repository during setup")
    orro_setup.add_argument("--depone-ref", default=None, help="pin setup-time Depone provisioning to this ref")
    orro_setup.add_argument("--json", action="store_true", help="emit machine-readable output")
    orro_setup.add_argument(
        "--yes",
        action="store_true",
        help="acknowledge setup-time provisioning without prompting",
    )
    orro_setup.set_defaults(func=_cli_handler("bootstrap", "_cmd_orro_setup"))

    scout = sub.add_parser("scout", help="run read-only ORRO repo scout")
    scout.add_argument("goal")
    scout.add_argument("--repo", "--root", dest="repo", default=".")
    scout.add_argument("--home", default=None)
    scout.add_argument("--out-dir", default=None)
    scout.set_defaults(func=_cli_handler("bootstrap", "_cmd_scout"))

    run = sub.add_parser("run", help="observe a lane and emit signed evidence")
    _add_run_args(run)
    run.set_defaults(func=_cli_handler("run", "_cmd_run"))

    proofrun = sub.add_parser(
        "proofrun",
        help="ORRO evidence-backed execution alias; emits evidence without final trust",
        description=(
            "ORRO evidence-backed execution alias. A direct shell invocation "
            "(`--adapter shell -- <command>`) is capture-only and is not "
            "proofcheckable by itself. For a proofcheckable packet, run `orro "
            "scout` first and continue through `flowplan -> proofrun -> "
            "proofcheck` in the same run directory, or use `orro team go`."
        ),
    )
    _add_run_args(proofrun)
    proofrun.add_argument(
        "--roadmap-item",
        default=None,
        metavar="ID",
        help="explicit .orro/roadmap.json item id for this run; never inferred",
    )
    proofrun.add_argument("--roadmap-step", default=None, metavar="ID")
    proofrun.add_argument(
        "--allow-reference-adapter",
        action="store_true",
        help=(
            "allow deterministic W18 fallback or role-lane shell reference/placeholder "
            "proofrun lanes for intentional script/test use; marked as not real AI work"
        ),
    )
    proofrun.set_defaults(func=_cli_handler("run", "_cmd_run"))

    a2 = sub.add_parser(
        "a2-observer-run",
        help=(
            "run one observer-launched shell lane; A2 independence additionally "
            "requires an external operator key and real observer/runner separation"
        ),
    )
    a2.add_argument(
        "--runner-sandbox",
        required=True,
        metavar="DIR",
        help=RUNNER_SANDBOX_HELP,
    )
    a2.add_argument("--out", required=True, help="observer evidence directory")
    a2.add_argument("--observer-dir", required=True)
    a2.add_argument("--keys-dir", default=None)
    a2.add_argument("--runner-user", default="ubuntu")
    a2.add_argument("--runner-uid", type=int, default=None)
    a2.add_argument("--task-id", default="w12-real-a2")
    a2.add_argument("--test-command", default=None)
    a2.add_argument("--allow", action="append", default=[])
    a2.add_argument("command", nargs=argparse.REMAINDER)
    a2.set_defaults(func=_cli_handler("team_ops", "_cmd_a2_observer_run"))

    plan = sub.add_parser(
        "plan",
        help="compatibility name for flowplan; emits a sealed plan without execution",
    )
    _add_plan_args(plan)
    plan.set_defaults(func=_cli_handler("plan", "_cmd_plan"))

    flowplan = sub.add_parser(
        "flowplan",
        help="ORRO plan-only workflow design; emits a sealed plan without execution",
    )
    _add_flowplan_args(flowplan)
    flowplan.set_defaults(func=_cli_handler("plan", "_cmd_plan"))

    status = sub.add_parser("status", help="render evidence-pending status")
    status.add_argument("--evidence-dir", default=".")
    status.add_argument("--runlog", default=None)
    status.set_defaults(func=_cli_handler("runtime_ops", "_cmd_status"))

    verify = sub.add_parser("verify", help="verify a run directory or runlog integrity")
    verify.add_argument("run_dir", nargs="?")
    verify.add_argument("--home", default=None)
    verify.add_argument("--runlog", default=None)
    verify.set_defaults(func=_cli_handler("runtime_ops", "_cmd_verify"))

    proofcheck = sub.add_parser(
        "proofcheck",
        help="ORRO offline proof verification wrapper delegated to Depone",
    )
    proofcheck.add_argument("evidence_dir", nargs="?")
    proofcheck.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    proofcheck.add_argument("--home", default=None)
    proofcheck.add_argument("--out", default=None)
    proofcheck.add_argument("--json", action="store_true")
    proofcheck.set_defaults(func=_cli_handler("verify", "_cmd_proofcheck"))

    advisory_provenance_check = sub.add_parser(
        "advisory-provenance-check",
        help="offline Depone v110 check for sealed advisory provenance only",
    )
    advisory_provenance_check.add_argument("evidence_dir")
    advisory_provenance_check.add_argument("--home", required=True)
    advisory_provenance_check.add_argument("--json", action="store_true")
    advisory_provenance_check.set_defaults(
        func=_cli_handler("verify", "_cmd_advisory_provenance_check")
    )

    handoff = sub.add_parser(
        "handoff",
        help="package ORRO evidence hashes and verifier decision references",
    )
    handoff.add_argument("evidence_dir", nargs="?")
    handoff.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    handoff.add_argument("--home", default=None)
    handoff.add_argument("--out", default=None)
    handoff.add_argument("--json", action="store_true")
    handoff.set_defaults(func=_cli_handler("verify", "_cmd_handoff"))

    route = sub.add_parser("route", help="dry-run W4 model routing")
    route.add_argument("--root", "--repo", dest="root", default=".")
    route.add_argument("--runlog", default=None)
    route.add_argument("--task-id", default="witnessd-route")
    route.add_argument(
        "--tier", required=True, choices=["quick", "agentic", "frontier"]
    )
    route.add_argument("--unsupported-model", action="append", default=[])
    route.set_defaults(func=_cli_handler("bootstrap", "_cmd_route"))

    doctor = sub.add_parser(
        "doctor", help="report runlog health; not ORRO engine/verifier readiness"
    )
    doctor.add_argument("--runlog", default=None)
    doctor.add_argument("--root", "--repo", dest="root", default=".")
    doctor.add_argument("--external-worktree", action="append", default=[])
    doctor.set_defaults(func=_cli_handler("runtime_ops", "_cmd_doctor"))

    orro_doctor = sub.add_parser(
        "orro-doctor",
        help=argparse.SUPPRESS,
        description=(
            "Report ORRO engine/verifier readiness; not runlog health or evidence "
            "verification."
        ),
    )
    orro_doctor.add_argument("--home", default=None)
    orro_doctor.add_argument(
        "--adapter",
        action="append",
        default=None,
        choices=["codex", "claude", "agy", "gemini", "opencode"],
    )
    orro_doctor.add_argument("--json", action="store_true")
    orro_doctor.add_argument("--engine-lock", default=None)
    orro_doctor.set_defaults(func=_cli_handler("verify", "_cmd_orro_doctor"))

    engine_lock = sub.add_parser(
        "engine-lock",
        help="write/check ORRO distribution metadata for pinned engine commits",
    )
    engine_lock.add_argument("--home", default=None)
    engine_lock.add_argument("--out", default=None)
    engine_lock.add_argument("--check", default=None)
    engine_lock.add_argument("--json", action="store_true")
    engine_lock.set_defaults(func=_cli_handler("verify", "_cmd_orro_engine_lock"))

    orro_next = sub.add_parser("orro-next", help=argparse.SUPPRESS)
    orro_next.add_argument("run_dir", nargs="?")
    orro_next.add_argument("--latest", action="store_true")
    orro_next.add_argument("--home", default=None)
    orro_next.add_argument("--out", default=None)
    orro_next.add_argument("--json", action="store_true")
    orro_next.set_defaults(func=_cli_handler("advisory", "_cmd_orro_next"))

    orro_advise = sub.add_parser("orro-advise", help=argparse.SUPPRESS)
    orro_advise.add_argument("goal", nargs="?")
    orro_advise.add_argument("--repo", "--root", dest="repo", default=".")
    orro_advise.add_argument("--home", default=None)
    orro_advise.add_argument("--out", default=None)
    orro_advise.add_argument("--mode", choices=["auto", "route", "sketch", "trace"], default="auto")
    orro_advise.add_argument("--decision", default=None, help=argparse.SUPPRESS)
    orro_advise.add_argument("--intent", default=None, help=argparse.SUPPRESS)
    orro_advise.add_argument("--_deprecated-alias", dest="_deprecated_alias", default=None, help=argparse.SUPPRESS)
    orro_advise.add_argument("--json", action="store_true")
    orro_advise.set_defaults(func=_cli_handler("advisory", "_cmd_orro_advise"))

    orro_sketch = sub.add_parser(
        "orro-sketch",
        help=argparse.SUPPRESS,
        description=(
            "Validate and seal an agent-authored advisory sketch decision. Without "
            "--decision, emits a degraded heuristic scaffold. Not proof or assurance."
        ),
    )
    orro_sketch.add_argument("goal", nargs="?")
    orro_sketch.add_argument("--repo", "--root", dest="repo", default=".")
    orro_sketch.add_argument("--home", default=None)
    orro_sketch.add_argument(
        "--decision",
        default=None,
        metavar="DECISION_JSON_PATH",
        help=(
            "path to a JSON file; required schema: frame, non-empty candidates[] "
            "with axis, summary, benefits[], risks[], and tradeoff or tradeoffs, "
            "chosen{direction, reason, confidence, what_would_change_it}, rejected[], "
            "no_gos[], and rabbit_holes[]. Example: "
            "tests/fixtures/orro-sketch-decision.json"
        ),
    )
    orro_sketch.add_argument(
        "--intent",
        default=None,
        metavar="INTENT_JSON_PATH",
        help=(
            "path to a JSON file; schema: {intent: str, non_goals?: [str], "
            "constraints?: [str]}. Example: tests/fixtures/orro-declared-intent.json"
        ),
    )
    orro_sketch.add_argument("--out", default=None)
    orro_sketch.add_argument("--json", action="store_true")
    orro_sketch.set_defaults(func=_cli_handler("advisory", "_cmd_orro_sketch"))

    orro_trace = sub.add_parser(
        "orro-trace",
        help=argparse.SUPPRESS,
        description=(
            "Validate, gate, and seal an agent-authored root cause decision. Without "
            "--decision, emits a degraded heuristic scaffold. Not proof or assurance."
        ),
    )
    orro_trace.add_argument("goal", nargs="?")
    orro_trace.add_argument("--repo", "--root", dest="repo", default=".")
    orro_trace.add_argument("--home", default=None)
    orro_trace.add_argument(
        "--decision",
        default=None,
        metavar="DECISION_JSON_PATH",
        help=(
            "path to a JSON file; required schema: check_the_plug{}, "
            "reproduction{path, sha256}, localization, hypotheses[] with mechanism, "
            "prediction, discriminating_probe, and confidence, confirmation{}, "
            "fix_scope{}, and exactly one of root_cause{} or unconfirmed"
        ),
    )
    orro_trace.add_argument("--out", default=None)
    orro_trace.add_argument("--json", action="store_true")
    orro_trace.set_defaults(func=_cli_handler("advisory", "_cmd_orro_trace"))

    orro_report = sub.add_parser("orro-report", help=argparse.SUPPRESS)
    orro_report.add_argument("run_dir", nargs="?")
    orro_report.add_argument("--latest", action="store_true")
    orro_report.add_argument("--home", default=None)
    orro_report.add_argument("--out", default=None)
    orro_report.add_argument("--workstyle-decision", default=None)
    orro_report.add_argument(
        "--intent",
        default=None,
        metavar="INTENT_JSON_PATH",
        help=(
            "path to a JSON file; schema: {intent: str, non_goals?: [str], "
            "constraints?: [str]}. Example: tests/fixtures/orro-declared-intent.json"
        ),
    )
    orro_report.add_argument("--json", action="store_true")
    orro_report.set_defaults(func=_cli_handler("advisory", "_cmd_orro_report"))

    orro_review = sub.add_parser(
        "orro-review",
        help=argparse.SUPPRESS,
        description=(
            "Run review-only role lanes through advisory read-only adapters. "
            "Not proofrun, not proofcheck, and not assurance."
        ),
    )
    orro_review.add_argument("--repo", "--root", dest="repo", required=True)
    orro_review.add_argument("--home", default=None)
    orro_review.add_argument("--run-dir", default=None)
    orro_review.add_argument("--role-lane-plan", required=True)
    orro_review.add_argument("--claude-binary", default="claude")
    orro_review.add_argument("--agy-binary", default="agy")
    orro_review.add_argument("--gemini-binary", default="gemini")
    orro_review.add_argument("--timeout-seconds", type=int, default=120)
    orro_review.add_argument("--json", action="store_true")
    orro_review.set_defaults(func=_cli_handler("advisory", "_cmd_orro_review"))

    orro_check = sub.add_parser(
        "orro-check",
        help=argparse.SUPPRESS,
        description=(
            "Companion: verify already-driven work with deterministic checks "
            "(Depone verdict) and review it read-only (advisory). Spawns zero "
            "execution-adapter lanes; does not claim observed execution."
        ),
    )
    orro_check.add_argument("--repo", "--root", dest="repo", default=None)
    orro_check.add_argument("--home", default=None)
    orro_check.add_argument("--run-dir", default=None)
    orro_check.add_argument(
        "--roadmap-item",
        default=None,
        metavar="ID",
        help="explicit .orro/roadmap.json item id for proofrun; never inferred",
    )
    orro_check.add_argument("--roadmap-step", default=None, metavar="ID")
    orro_check.add_argument("--check", action="append", default=None)
    orro_check.add_argument(
        "--health",
        action="store_true",
        help="detect the repo's already-adopted deterministic gates and add them",
    )
    orro_check.add_argument(
        "--fix",
        action="store_true",
        help="run only safe configured fixers before verify; requires explicit --write-scope",
    )
    orro_check.add_argument(
        "--write-scope",
        action="append",
        default=[],
        metavar="'<glob>'",
        help="repeatable fixer write bound; mandatory with --fix and never inferred",
    )
    orro_check.add_argument(
        "--apply",
        action="store_true",
        help="apply the scope-verified fixer diff to the caller's working tree",
    )
    orro_check.add_argument(
        "--health-plan",
        action="store_true",
        help="print the detected health gate plan as JSON without running phases",
    )
    orro_check.add_argument(
        "--init",
        action="store_true",
        help="append missing default tool config, seed .orro/health.json, then run health",
    )
    orro_check.add_argument(
        "--promote",
        action="append",
        default=[],
        metavar="GATE",
        help="repeatable: promote a profile advisory gate to block, then run health",
    )
    orro_check.add_argument(
        "--intent",
        default=None,
        metavar="INTENT_JSON_PATH",
        help=(
            "path to a JSON file; schema: {intent: str, non_goals?: [str], "
            "constraints?: [str]}. Example: tests/fixtures/orro-declared-intent.json"
        ),
    )
    orro_check.add_argument(
        "--reviewer", default=None, choices=["agy", "gemini", "claude"]
    )
    orro_check.add_argument("--reviewer-binary", default=None)
    orro_check.add_argument("--no-review", action="store_true")
    orro_check.add_argument("--base", default=None)
    orro_check.add_argument("--timeout-seconds", type=int, default=120)
    orro_check.add_argument("--json", action="store_true")
    orro_check.set_defaults(func=_cli_handler("companion", "_cmd_orro_check"))

    orro_auto = sub.add_parser(
        "orro-auto",
        help=argparse.SUPPRESS,
        description=(
            "auto --run-item executes the next declared step's recommended command "
            "behind evidence gates; bounded by --max-steps; stops at the first non-pass. "
            "Existing --dry-run, --once, and --until-complete modes remain non-proofrun."
        ),
    )
    orro_auto.add_argument("run_dir", nargs="?")
    orro_auto.add_argument("--latest", action="store_true")
    orro_auto.add_argument("--dry-run", action="store_true")
    orro_auto.add_argument("--once", action="store_true")
    orro_auto.add_argument("--until-complete", action="store_true")
    orro_auto.add_argument("--run-item", default=None, metavar="ITEM_ID")
    orro_auto.add_argument("--repo", default=None)
    orro_auto.add_argument("--max-steps", type=int, default=None)
    orro_auto.add_argument("--home", default=None)
    orro_auto.add_argument("--out", default=None)
    orro_auto.add_argument("--json", action="store_true")
    orro_auto.set_defaults(func=_cli_handler("advisory", "_cmd_orro_auto"))

    orro_flow = sub.add_parser(
        "orro-flow",
        help=argparse.SUPPRESS,
        description=(
            "Guided init -> scout -> flowplan -> proofrun -> proofcheck orchestration. "
            "Existing gates remain authoritative and are surfaced as blockers."
        ),
    )
    orro_flow.add_argument("goal")
    orro_flow.add_argument("--repo", "--root", dest="repo", default=None)
    orro_flow.add_argument("--write-scope", action="append", default=[])
    orro_flow.add_argument(
        "--command",
        action="append",
        default=None,
        metavar="'<shell>'",
        help=(
            "--command '<shell>' (repeatable, --lane-adapter shell only): declared "
            "deterministic commands the runner executes; touched files are checked "
            "against --write-scope. Not for AI adapters."
        ),
    )
    orro_flow.add_argument(
        "--adapter",
        default=None,
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    orro_flow.add_argument("--home", default=None)
    orro_flow.add_argument("--depone-root", default=None, help="pass a local Depone checkout to the init phase")
    orro_flow.add_argument("--allow-network", action="store_true", help="allow setup-time network provisioning in the init phase")
    orro_flow.add_argument(
        "--runner-sandbox",
        default=None,
        metavar="DIR",
        help=RUNNER_SANDBOX_HELP,
    )
    orro_flow.add_argument("--rolepack-file", default=None)
    orro_flow.add_argument(
        "--role-lane-tier",
        default="auto",
        choices=["auto", "quick", "agentic", "frontier"],
        help=(
            "auto (default): shell lanes run at quick/120s, AI-adapter lanes at "
            "agentic/1800s; override with quick|agentic|frontier"
        ),
    )
    orro_flow.add_argument("--run-dir", default=None)
    orro_flow.add_argument(
        "--roadmap-item",
        default=None,
        metavar="ID",
        help="explicit .orro/roadmap.json item id for proofrun; never inferred",
    )
    orro_flow.add_argument("--roadmap-step", default=None, metavar="ID")
    orro_flow.add_argument("--allow-reference-adapter", action="store_true")
    orro_flow.add_argument("--json", action="store_true")
    orro_flow.add_argument("--verification-only", action="store_true")
    orro_flow.set_defaults(func=_cli_handler("flow", "_cmd_orro_flow"))

    orro_demo = sub.add_parser(
        "orro-demo",
        help=argparse.SUPPRESS,
        description=(
            "Run an offline deterministic shell guardrail demonstration through "
            "witnessd execution and Depone policy-conformance re-derivation."
        ),
    )
    orro_demo.add_argument("--violate", action="store_true")
    orro_demo.add_argument("--work-dir", default=None)
    orro_demo.add_argument("--depone-root", default=None)
    orro_demo.set_defaults(func=_cli_handler("demo", "_cmd_orro_demo"))

    orro_status = sub.add_parser(
        "orro-status",
        help=argparse.SUPPRESS,
        description="Report roadmap-bound observed run state without executing or verifying.",
    )
    orro_status.add_argument("--repo", "--root", dest="repo", default=".")
    orro_status.add_argument("--home", default=None)
    orro_status.add_argument("--json", action="store_true")
    orro_status.set_defaults(func=_cli_handler("status", "_cmd_orro_status"))

    orro_tidy = sub.add_parser(
        "orro-tidy",
        help=argparse.SUPPRESS,
        description="Inventory ORRO worktrees or safely remove eligible clean worktrees.",
    )
    orro_tidy.add_argument("--repo", "--root", dest="repo", default=".")
    orro_tidy.add_argument("--home", default=None)
    orro_tidy.add_argument("--apply", action="store_true")
    orro_tidy.add_argument("--keep-checks", type=int, default=None)
    orro_tidy.add_argument("--json", action="store_true")
    orro_tidy.set_defaults(func=_cli_handler("status", "_cmd_orro_tidy"))

    orro_task = sub.add_parser(
        "orro-task",
        help=argparse.SUPPRESS,
        description=(
            "Manage a roadmap-item task worktree; setup metadata only, not proof. "
            "The worktree, its branch, and its commits are workspace state, not proof; "
            "task begin output is setup metadata — not proof, not verifier truth, not "
            "approval, not assurance. Merge approval and merge execution stay human; "
            "ORRO never merges. Panes/agent/session state belong to the workspace "
            "runtime, never sealed into evidence."
        ),
    )
    task_sub = orro_task.add_subparsers(dest="task_command", required=True)
    task_begin = task_sub.add_parser(
        "begin",
        help="create or resume a roadmap-item task worktree",
        description=(
            "Create or resume the persistent roadmap-item worktree. Output is setup "
            "metadata only, not proof, verifier truth, approval, or assurance."
        ),
    )
    task_begin.add_argument("item_id")
    task_begin.add_argument("--repo", "--root", dest="repo", default=".")
    task_begin.add_argument("--base", default=None, help="base ref for a new task branch (default: current HEAD)")
    open_group = task_begin.add_mutually_exclusive_group()
    open_group.add_argument(
        "--open",
        action="store_true",
        help="run ORRO_TASK_OPEN_COMMAND even when resuming an existing task",
    )
    open_group.add_argument(
        "--no-open",
        action="store_true",
        help="skip ORRO_TASK_OPEN_COMMAND (recommended for non-interactive use)",
    )
    task_begin.add_argument("--json", action="store_true")
    task_begin.set_defaults(func=_cli_handler("task", "_cmd_orro_task"))

    isolation = sub.add_parser("isolation", help="isolation contract checks")
    isolation.add_argument("--self-test", action="store_true")
    isolation.set_defaults(func=_cli_handler("runtime_ops", "_cmd_isolation"))

    faultkit = sub.add_parser("faultkit", help="deterministic fault injection")
    faultkit_sub = faultkit.add_subparsers(dest="fault", required=True)
    zombie = faultkit_sub.add_parser("zombie-hang")
    zombie.add_argument("--runlog", required=True)
    zombie.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    crash = faultkit_sub.add_parser("crash-mid-toolcall")
    crash.add_argument("--runlog-before", required=True)
    crash.add_argument("--runlog-after", required=True)
    crash.add_argument("--session", required=True)
    crash.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    pause_race = faultkit_sub.add_parser("pause-race")
    pause_race.add_argument("--runlog", required=True)
    pause_race.add_argument("--run-id", default="faultkit-pause-run")
    pause_race.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    budget = faultkit_sub.add_parser("budget-blowout")
    budget.add_argument("--root", "--repo", dest="root", required=True)
    budget.add_argument(
        "--runner-sandbox",
        required=True,
        metavar="DIR",
        help=RUNNER_SANDBOX_HELP,
    )
    budget.add_argument("--codex-binary", default="codex")
    budget.add_argument("--task-id", default="budget-blowout")
    budget.add_argument("--prompt", default="trigger budget blowout")
    budget.add_argument("--max-tokens", type=int, default=1)
    budget.add_argument("--max-usd", type=float, default=10**9)
    budget.add_argument("--max-depth", type=int, default=3)
    budget.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))

    team = sub.add_parser("team", help="run a local team fan-in")
    team_sub = team.add_subparsers(dest="team_cmd", required=True)
    team_init = team_sub.add_parser(
        "init",
        help="scaffold .orro/team.json readiness configuration",
        description=(
            "Scaffold an ORRO rolepack. This writes readiness configuration only; "
            "it does not execute, verify evidence, or raise assurance."
        ),
    )
    team_init.add_argument("--out", default=".orro/team.json")
    from witnessd.orro_team_surface import valid_team_templates

    team_templates = valid_team_templates()
    team_init.add_argument(
        "--template",
        default="developer",
        help=f"team rolepack template (valid: {', '.join(team_templates)})",
    )
    team_init.add_argument("--role", action="append", default=[])
    team_init.add_argument("--write-scope", action="append", default=[])
    team_init.add_argument("--tool-mcp", action="append", default=[])
    team_init.add_argument("--tool-allow", action="append", default=[])
    team_init.add_argument("--interactive", action="store_true")
    team_init.add_argument("--yes", action="store_true")
    team_init.add_argument("--json", action="store_true")
    team_init.set_defaults(func=_cli_handler("team_ops", "_cmd_team_init"))

    team_go = team_sub.add_parser(
        "go",
        help="run flowplan, proofrun, proofcheck, and report for a team rolepack",
    )
    team_go.add_argument("goal")
    team_go.add_argument("--task", default=None)
    team_go.add_argument("--repo", "--root", dest="repo", required=True)
    team_go.add_argument("--home", default=None)
    team_go.add_argument("--team", default=None)
    team_go.add_argument("--run-dir", default=None)
    team_go.add_argument(
        "--roadmap-item",
        default=None,
        metavar="ID",
        help="explicit .orro/roadmap.json item id for proofrun; never inferred",
    )
    team_go.add_argument("--roadmap-step", default=None, metavar="ID")
    team_go.add_argument(
        "--profile",
        choices=[
            "code-change",
            "docs-change",
            "review-only",
            "verification-only",
            "release-readiness",
        ],
        default=None,
    )
    team_go.add_argument(
        "--role-lane-tier",
        default="auto",
        choices=["auto", "quick", "agentic", "frontier"],
        help=(
            "auto (default): shell lanes run at quick/120s, AI-adapter lanes at "
            "agentic/1800s; override with quick|agentic|frontier"
        ),
    )
    team_go.add_argument("--max-parallel", type=int, default=1)
    team_go.add_argument("--fail-fast", action="store_true")
    team_go.add_argument(
        "--allow-reference-adapter",
        action="store_true",
        help="allow shell reference/script proofrun lanes; flagged as not real AI work",
    )
    team_go.add_argument("--codex-binary", default="codex")
    team_go.add_argument("--claude-binary", default="claude")
    team_go.add_argument("--agy-binary", default="agy")
    team_go.add_argument("--gemini-binary", default="gemini")
    team_go.add_argument("--opencode-binary", default="opencode")
    team_go.add_argument("--json", action="store_true")
    team_go.set_defaults(func=_cli_handler("team_go", "_cmd_team_go"))

    team_run = team_sub.add_parser("run", help="emit team fan-in evidence")
    team_run.add_argument("--repo", "--root", dest="repo", required=True)
    team_run.add_argument("--out", required=True)
    team_run.add_argument("--keys-dir", default=None)
    team_run.add_argument("--state-root", default=None)
    team_run.add_argument("--codex-auth-source", default=None)
    team_run.add_argument("--max-parallel", type=int, default=None)
    team_run.add_argument("--fail-fast", action="store_true")
    team_run.add_argument("--lane-prompt-file", action="append", default=[])
    team_run.add_argument(
        "--merge-group",
        action="append",
        default=[],
        help="merge_lane:source_a,source_b:file[,file...] for explicit overlapped-source merge evidence",
    )
    team_run.add_argument(
        "--lane",
        action="append",
        required=True,
        help="lane_id:file[,file...] or lane_id:adapter=codex:tier=quick:region=file[,file...]:prompt=...",
    )
    team_run.set_defaults(func=_cli_handler("team_ops", "_cmd_team_run"))

    team_plan_run = team_sub.add_parser(
        "plan-run", help="plan a goal and run the resulting team lanes"
    )
    team_plan_run.add_argument("goal")
    team_plan_run.add_argument("--repo", "--root", dest="repo", required=True)
    team_plan_run.add_argument("--out", required=True)
    team_plan_run.add_argument("--keys-dir", default=None)
    team_plan_run.add_argument("--seed", default="w11")
    team_plan_run.add_argument(
        "--draft-adapter",
        choices=["heuristic", "codex", "claude", "opencode"],
        default="heuristic",
    )
    team_plan_run.add_argument(
        "--lane-adapter",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
        default="shell",
    )
    team_plan_run.add_argument("--tier", default="agentic")
    team_plan_run.add_argument("--max-tokens", type=int, default=10**9)
    team_plan_run.add_argument("--max-usd", type=float, default=10**9)
    team_plan_run.add_argument("--max-depth", type=int, default=3)
    team_plan_run.add_argument(
        "--lane-timeout",
        type=int,
        default=DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS,
        help="whole-lane adapter timeout in seconds (default: 900)",
    )
    team_plan_run.add_argument("--state-root", default=None)
    team_plan_run.add_argument("--codex-auth-source", default="~/.codex/auth.json")
    team_plan_run.add_argument("--codex-binary", default="codex")
    team_plan_run.add_argument("--claude-binary", default="claude")
    team_plan_run.add_argument("--agy-binary", default="agy")
    team_plan_run.add_argument("--gemini-binary", default="gemini")
    team_plan_run.add_argument("--opencode-binary", default="opencode")
    team_plan_run.add_argument("--max-parallel", type=int, default=None)
    team_plan_run.add_argument("--fail-fast", action="store_true")
    team_plan_run.set_defaults(func=_cli_handler("team_ops", "_cmd_team_plan_run"))

    team_ledger = sub.add_parser(
        "team-ledger", help="show team-ledger status pending Depone verification"
    )
    team_ledger.add_argument("--ledger", required=True)
    team_ledger.add_argument("--json", action="store_true")
    team_ledger.set_defaults(func=_cli_handler("team_ops", "_cmd_team_ledger"))

    team_resume_audit = team_sub.add_parser(
        "resume-audit", help="audit surviving team lane bytes without replay"
    )
    team_resume_audit.add_argument("--out", required=True)
    team_resume_audit.add_argument("--run-id", default="w15-resume-audit")
    team_resume_audit.add_argument("--json", action="store_true")
    team_resume_audit.set_defaults(
        func=_cli_handler("team_ops", "_cmd_team_resume_audit")
    )

    team_resume = team_sub.add_parser("resume", help="resume an interrupted team run")
    team_resume.add_argument("run_dir")
    team_resume.add_argument("--run-id", default="w3-team")
    team_resume.add_argument("--max-parallel", type=int, default=None)
    team_resume.add_argument("--fail-fast", action="store_true")
    team_resume.add_argument("--json", action="store_true")
    team_resume.set_defaults(func=_cli_handler("team_ops", "_cmd_team_resume"))

    team_kill = team_sub.add_parser("kill", help="kill all live team lanes")
    team_kill.add_argument("--runlog", default=None)
    team_kill.add_argument("--state-root", default=None)
    team_kill.add_argument("--run-id", default="team-kill")
    team_kill.add_argument("--all", action="store_true", default=True)
    team_kill.set_defaults(func=_cli_handler("team_ops", "_cmd_team_kill"))

    lane_exec = sub.add_parser("lane-exec", help=argparse.SUPPRESS)
    lane_exec.add_argument("--spec-json", required=True)
    lane_exec.add_argument("--result-json", required=True)
    lane_exec.set_defaults(func=_cli_handler("team_ops", "_cmd_lane_exec"))

    pause = sub.add_parser("pause", help="append a user pause event")
    pause.add_argument("run_id")
    pause.add_argument("--runlog", required=True)
    pause.set_defaults(func=_cli_handler("runtime_ops", "_cmd_pause"))

    resume = sub.add_parser("resume", help="append an explicit user resume event")
    resume.add_argument("run_id")
    resume.add_argument("--runlog", required=True)
    resume.add_argument("--confirm", action="store_true")
    resume.set_defaults(func=_cli_handler("runtime_ops", "_cmd_resume_pause"))

    kill = sub.add_parser("kill", help="kill all supervised children")
    kill.add_argument("--all", action="store_true")
    kill.add_argument("--runlog", required=True)
    kill.add_argument("--run-id", default="witnessd-kill")
    kill.set_defaults(func=_cli_handler("runtime_ops", "_cmd_kill"))

    learn = sub.add_parser("learn", help="learning delta commands")
    learn_sub = learn.add_subparsers(dest="learn_cmd", required=True)
    promote = learn_sub.add_parser("promote")
    promote.add_argument("--delta", required=True)
    promote.add_argument("--capture", action="append", required=True)
    promote.add_argument("--approval-log", action="append", default=[])
    promote.add_argument("--runlog", required=True)
    promote.add_argument("--run-id", default="witnessd-learning")
    promote.add_argument("--private-key", required=True)
    promote.add_argument("--public-key", required=True)
    promote.add_argument("--evidence-dir", required=True)
    promote.add_argument("--bundle-out", default=None)
    promote.set_defaults(func=_cli_handler("runtime_ops", "_cmd_learn"))

    for name, help_text in (
        ("install", "atomically install witnessd payload"),
        ("upgrade", "atomically upgrade witnessd payload"),
    ):
        install = sub.add_parser(name, help=help_text)
        install.add_argument("--payload", required=True)
        install.add_argument("--dest", required=True)
        install.add_argument("--config", required=True)
        install.add_argument("--shim-dir", required=True)
        install.add_argument("--version", required=True)
        install.add_argument("--root", "--repo", dest="root", default=".")
        install.add_argument("--runlog", default=None)
        install.set_defaults(func=_cli_handler("runtime_ops", "_cmd_install"))

    self_test = sub.add_parser("self-test", help="run module self-tests")
    self_test.add_argument("--all", action="store_true")
    self_test.set_defaults(func=_cli_handler("self_test", "_cmd_self_test"))

    pilot = sub.add_parser("pilot", help="external-team pilot tooling")
    pilot_sub = pilot.add_subparsers(dest="pilot_cmd", required=True)

    pilot_init = pilot_sub.add_parser("init", help="create a pilot deployment record")
    pilot_init.add_argument("--operator", required=True)
    pilot_init.add_argument("--team-scope", required=True)
    pilot_init.add_argument("--out", required=True)
    pilot_init.add_argument("--deployed-runtime", action="store_true")
    pilot_init.add_argument("--not-dogfood", action="store_true")
    pilot_init.add_argument("--not-ci", action="store_true")
    pilot_init.add_argument(
        "--deployment-root",
        default=None,
        help="repo path of the deployed runtime whose git SHA is recorded (default: this tree)",
    )
    pilot_init.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_init"))

    pilot_close = pilot_sub.add_parser("close", help="close a pilot deployment record")
    pilot_close.add_argument("--record", required=True)
    pilot_close.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_close"))

    pilot_rotation = pilot_sub.add_parser(
        "rotation-record", help="create an operator key rotation record"
    )
    pilot_rotation.add_argument("--archive", required=True)
    pilot_rotation.add_argument("--out", required=True)
    pilot_rotation.add_argument("--retired-key-id", default="witnessd-operator")
    pilot_rotation.set_defaults(
        func=_cli_handler("pilot", "_cmd_pilot_rotation_record")
    )

    pilot_canary = pilot_sub.add_parser(
        "canary", help="emit a signed operator key-rotation canary bundle"
    )
    pilot_canary.add_argument("--keys-dir", required=True)
    pilot_canary.add_argument("--out", required=True)
    pilot_canary.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_canary"))

    pilot_archive = pilot_sub.add_parser(
        "archive-evidence", help="record pilot evidence paths and hashes"
    )
    pilot_archive.add_argument("--archive", required=True)
    pilot_archive.add_argument("--out", default=None)
    pilot_archive.add_argument("--artifact", action="append", required=True)
    pilot_archive.set_defaults(
        func=_cli_handler("pilot", "_cmd_pilot_archive_evidence")
    )

    return parser


def _add_plan_args(plan: argparse.ArgumentParser) -> None:
    plan.add_argument("goal")
    plan.add_argument("--root", "--repo", dest="root", default=".")
    plan.add_argument("--seed", default="w11")
    plan.add_argument(
        "--draft-adapter", choices=["codex", "claude", "agy", "gemini", "opencode"]
    )
    plan.add_argument("--draft-out", default=None)
    plan.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    plan.add_argument("--codex-binary", default="codex")
    plan.add_argument("--claude-binary", default="claude")
    plan.add_argument("--agy-binary", default="agy")
    plan.add_argument("--gemini-binary", default="gemini")
    plan.add_argument("--opencode-binary", default="opencode")
    plan.add_argument("--max-tokens", type=int, default=10**9)
    plan.add_argument("--max-usd", type=float, default=10**9)
    plan.add_argument("--max-depth", type=int, default=3)
    plan.add_argument("--predicted-tokens", type=int, default=0)
    plan.add_argument("--predicted-usd", type=float, default=0.0)


def _add_run_args(run: argparse.ArgumentParser) -> None:
    run.add_argument("--goal", default=None, help=argparse.SUPPRESS)
    run.add_argument("--task", default=None, help=argparse.SUPPRESS)
    run.add_argument("--repo", default=None)
    run.add_argument("--home", default=None)
    run.add_argument("--run-dir", default=None)
    run.add_argument("--workflow-plan", default=None)
    run.add_argument("--role-lane-plan", default=None)
    run.add_argument("--json", action="store_true")
    run.add_argument("--max-parallel", type=int, default=None)
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument(
        "--adapter",
        default="shell",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    run.add_argument("--root", default=".")
    run.add_argument(
        "--runner-sandbox",
        default=None,
        metavar="DIR",
        help=RUNNER_SANDBOX_HELP,
    )
    run.add_argument(
        "--out", default=None, help="observer output path (outside sandbox)"
    )
    run.add_argument("--log", default=None, help="observer log path (outside sandbox)")
    run.add_argument("--keys-dir", default=None)
    run.add_argument("--task-id", default="witnessd-lane")
    run.add_argument(
        "--arm",
        default="direct",
        choices=["direct", "governed"],
        help=(
            "select the execution arm; --arm direct does not convert a self-signed "
            "trust anchor into an independent trust anchor"
        ),
    )
    run.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    run.add_argument("--codex-binary", default="codex")
    run.add_argument("--claude-binary", default="claude")
    run.add_argument("--agy-binary", default="agy")
    run.add_argument("--gemini-binary", default="gemini")
    run.add_argument("--opencode-binary", default="opencode")
    run.add_argument("--max-tokens", type=int, default=10**9)
    run.add_argument("--max-usd", type=float, default=10**9)
    run.add_argument("--max-depth", type=int, default=3)
    run.add_argument("--predicted-tokens", type=int, default=0)
    run.add_argument("--predicted-usd", type=float, default=0.0)
    run.add_argument(
        "--allow", action="append", default=[], help="allowed touched file"
    )
    run.add_argument(
        "--capture-profile",
        choices=["full", "redacted"],
        default="redacted",
    )
    run.add_argument(
        "--keyless",
        action="store_true",
        help=(
            "opt-in Sigstore keyless signing; permanently publishes identity and "
            "evidence hash to public Rekor and fails closed if unavailable"
        ),
    )
    run.add_argument(
        "--signing-profile",
        choices=["operator-key", "keyless-fulcio-rekor"],
        default=None,
        help=(
            "signing profile; keyless is opt-in, publishes to public Rekor, and "
            "fails closed if unavailable"
        ),
    )
    run.add_argument(
        "--oauth-force-oob",
        action="store_true",
        help="use Sigstore's headless-server out-of-band OAuth flow",
    )
    run.add_argument(
        "--identity-token",
        default=None,
        help="explicit Sigstore identity token for non-interactive keyless signing",
    )
    run.add_argument(
        "--staging",
        action="store_true",
        help="use Sigstore staging for an opt-in keyless test run",
    )
    run.add_argument("command", nargs=argparse.REMAINDER)


def _add_flowplan_args(flowplan: argparse.ArgumentParser) -> None:
    flowplan.epilog = "For automatic path threading, use `orro flow` / `orro team go`."
    flowplan.add_argument("goal")
    flowplan.add_argument("--root", "--repo", dest="root", default=".")
    flowplan.add_argument("--seed", default="w11")
    flowplan.add_argument("--profile", default=None)
    flowplan.add_argument(
        "--lane-intent",
        choices=["implementation", "verification-only"],
        default=None,
    )
    flowplan.add_argument(
        "--check",
        action="append",
        default=None,
        help="declared verification check command for verification-only role "
        "lanes (repeatable; requires --role-lanes-out)",
    )
    flowplan.add_argument(
        "--command",
        action="append",
        default=None,
        metavar="'<shell>'",
        help=(
            "--command '<shell>' (repeatable, --lane-adapter shell only): declared "
            "deterministic commands the runner executes; touched files are checked "
            "against --write-scope. Not for AI adapters."
        ),
    )
    flowplan.add_argument("--out", default=None)
    flowplan.add_argument("--role-lanes-out", default=None)
    flowplan.add_argument(
        "--write-scope",
        action="append",
        default=[],
        metavar="'<glob>'",
        help=(
            "--write-scope '<glob>' (repeatable): bounded write scope for a "
            "code-change plan; generates the role capability directly instead of "
            "requiring a prebuilt rolepack. Never inferred or defaulted."
        ),
    )
    flowplan.add_argument(
        "--lane-adapter",
        default="shell",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    flowplan.add_argument(
        "--role-lane-tier",
        default="auto",
        choices=["auto", "quick", "agentic", "frontier"],
        help=(
            "auto (default): shell lanes run at quick/120s, AI-adapter lanes at "
            "agentic/1800s; override with quick|agentic|frontier"
        ),
    )
    flowplan.add_argument(
        "--lane-timeout-seconds",
        type=int,
        default=None,
        help="override the tier-derived execution lane timeout (1..3600 seconds)",
    )
    flowplan.add_argument(
        "--model-policy",
        default="off",
        choices=["off", "default"],
        help="off (default) keeps --lane-adapter uniform across lanes; "
        "default resolves each lane's (role, tier) to a policy adapter/model",
    )
    flowplan.add_argument(
        "--rolepack",
        default=None,
        help="named rolepack to apply when compiling --role-lanes-out",
    )
    flowplan.add_argument(
        "--rolepack-file",
        default=None,
        help="JSON rolepack file to apply when compiling --role-lanes-out",
    )
    flowplan.add_argument(
        "--team",
        default=None,
        help="onboarding rolepack JSON file (usually .orro/team.json)",
    )
    flowplan.add_argument("--json", action="store_true")
    flowplan.set_defaults(
        draft_adapter=None,
        draft_out=None,
        tier="agentic",
        codex_binary="codex",
        claude_binary="claude",
        agy_binary="agy",
        gemini_binary="gemini",
        opencode_binary="opencode",
        max_tokens=10**9,
        max_usd=10**9,
        max_depth=3,
        predicted_tokens=0,
        predicted_usd=0.0,
    )


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_run_goal_argv(
        _normalize_orro_argv(
            _normalize_superflow_argv(list(sys.argv[1:] if argv is None else argv))
        )
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it so command is the argv.
    if getattr(args, "command", None) and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


def _normalize_run_goal_argv(argv: list[str]) -> list[str]:
    if (
        len(argv) < 2
        or argv[0] not in {"run", "proofrun"}
        or "--" in argv
        or "--goal" in argv
    ):
        return argv
    first = argv[1]
    if first.startswith("-"):
        return argv
    return [argv[0], "--goal", first, *argv[2:]]


def _normalize_superflow_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "superflow":
        return argv
    if len(argv) >= 2 and argv[1] == "scout":
        return ["scout", *argv[2:]]
    return argv


ORRO_COMMAND_MAP: dict[str, str] = {
    "setup": "orro-setup",
    "init": "init",
    "scout": "scout",
    "flowplan": "flowplan",
    "proofrun": "proofrun",
    "proofcheck": "proofcheck",
    "advisory-provenance-check": "advisory-provenance-check",
    "handoff": "handoff",
    "doctor": "orro-doctor",
    "engine-lock": "engine-lock",
    "lock": "engine-lock",
    "next": "orro-next",
    "advise": "orro-advise",
    "sketch": "orro-sketch",
    "trace": "orro-trace",
    "report": "orro-report",
    "review": "orro-review",
    "check": "orro-check",
    "demo": "orro-demo",
    "status": "orro-status",
    "tidy": "orro-tidy",
    "task": "orro-task",
    "auto": "orro-auto",
    "flow": "orro-flow",
    "team": "team",
}
PUBLIC_COMMAND_SUMMARIES: dict[str, str] = {
    "setup": "provision pinned Depone, initialize home, and write engine lock",
    "init": "setup readiness/provision metadata; does not verify evidence",
    "scout": "read-only repository exploration and context packaging",
    "flowplan": "plan-only workflow design; does not run workers",
    "proofrun": "evidence-backed execution through witnessd",
    "proofcheck": "offline evidence verification delegated to Depone",
    "advisory-provenance-check": "offline Depone v110 re-derivation of sealed advisory provenance",
    "handoff": "maintainer review package gated by proofcheck-verdict.json",
    "doctor": "ORRO engine/verifier readiness; not runlog health or evidence verification",
    "engine-lock": "write/check distribution metadata for pinned engine commits",
    "lock": "alias for engine-lock",
    "next": "non-executing continuation gate over persisted run artifacts",
    "advise": "non-executing workstyle router for the smallest safe workflow",
    "sketch": "validate and seal an agent-authored advisory direction",
    "trace": "validate, gate, and seal an agent-authored root-cause record",
    "report": "human-facing summary of observed ORRO artifacts and next action",
    "review": "advisory read-only reviewer lanes; not proof or assurance",
    "check": "companion: verify (Depone verdict) plus read-only review; not observed execution",
    "demo": "AI-free shell guardrail demo with Depone scope-conformance result",
    "status": "roadmap-bound observed state; not proof, approval, or assurance",
    "tidy": "dry-run worktree inventory; apply removes only safe eligible worktrees",
    "task": "manage roadmap task lifecycle metadata; not proof or merge approval",
    "auto": "dry-run, one-step, bounded post-run, or bounded item-chain automation",
    "flow": "guided init/scout/flowplan/proofrun/proofcheck with gated blockers",
    "team": "scaffold team config or run flowplan/proofrun/proofcheck/report",
}
ORRO_COMMANDS: frozenset[str] = frozenset(ORRO_COMMAND_MAP)


def _normalize_orro_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "orro":
        return argv
    if len(argv) >= 2 and argv[1] in ORRO_COMMAND_MAP:
        command = argv[1]
        normalized = [ORRO_COMMAND_MAP[command], *argv[2:]]
        if command in {"sketch", "trace"}:
            return [
                "orro-advise",
                "--mode",
                command,
                "--_deprecated-alias",
                command,
                *argv[2:],
            ]
        return normalized
    return argv


if __name__ == "__main__":
    sys.exit(main())
