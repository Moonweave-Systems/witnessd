"""Opt-in measurement harness for static ORRO model routing.

This module is deliberately outside the proof/assurance path. Offline use only
plans measurements from the static policy; live execution is explicit and emits
measurement JSON, not a benchmark claim or verifier result.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.model_policy import DEFAULT_MODEL_POLICY, resolve_policy_route

MODEL_ROUTING_BENCHMARK_KIND = "moonweave-model-routing-measurement"
MODEL_ROUTING_BENCHMARK_SCHEMA_VERSION = "0.1"


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    role_kind: str
    tier: str
    prompt: str
    predicted_tokens: int
    predicted_usd: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role_kind": self.role_kind,
            "tier": self.tier,
            "prompt": self.prompt,
            "predicted_tokens": self.predicted_tokens,
            "predicted_usd": self.predicted_usd,
        }


def default_task_suite() -> list[BenchmarkTask]:
    prompts = [
        "Create a tiny documentation note.",
        "Make a focused one-file code edit.",
        "Review a small diff and summarize risks.",
        "Inspect a release checklist for blockers.",
    ]
    tasks: list[BenchmarkTask] = []
    index = 1
    for role_kind in ("runner", "reviewer"):
        for tier in ("quick", "agentic", "frontier"):
            for prompt in prompts:
                tasks.append(
                    BenchmarkTask(
                        task_id=f"mr-{index:02d}",
                        role_kind=role_kind,
                        tier=tier,
                        prompt=prompt,
                        predicted_tokens=1000,
                        predicted_usd=0.01,
                    )
                )
                index += 1
    return tasks


def measurement_boundary() -> dict[str, bool]:
    return {
        "proof": False,
        "assurance": False,
        "benchmark_claim": False,
        "can_change_evidence_verdict": False,
        "live_model_calls": False,
    }


def _live_boundary() -> dict[str, bool]:
    boundary = measurement_boundary()
    boundary["live_model_calls"] = True
    return boundary


def plan_measurements(
    tasks: list[BenchmarkTask],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or DEFAULT_MODEL_POLICY
    planned = []
    for task in tasks:
        route = resolve_policy_route(
            policy, role_kind=task.role_kind, tier=task.tier
        )
        if route is None:
            planned.append(
                {
                    **task.as_dict(),
                    "status": "blocked",
                    "blocked_reason": "ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED",
                    "budget_compliant": False,
                }
            )
            continue
        budget = dict(route["budget"])
        planned.append(
            {
                **task.as_dict(),
                "status": "planned",
                "route": {
                    "adapter": route["adapter"],
                    "model": route["model"],
                },
                "budget": budget,
                "budget_compliant": (
                    task.predicted_tokens <= int(budget["max_tokens"])
                    and task.predicted_usd <= float(budget["max_usd"])
                ),
                "degraded": False,
                "declared_model": route["model"],
                "actual_model": None,
                "declared_equals_actual": None,
            }
        )
    boundary = measurement_boundary()
    return {
        "kind": MODEL_ROUTING_BENCHMARK_KIND,
        "schema_version": MODEL_ROUTING_BENCHMARK_SCHEMA_VERSION,
        "live": False,
        "summary": {
            "task_count": len(planned),
            "planned_count": sum(1 for task in planned if task["status"] == "planned"),
            "blocked_count": sum(1 for task in planned if task["status"] == "blocked"),
        },
        "boundary": boundary,
        "tasks": planned,
    }


def write_measurement(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _init_measurement_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "benchmark@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "benchmark"], cwd=repo, check=True)
    (repo / "README.md").write_text("benchmark fixture\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _actual_model_from_declaration(declaration: dict[str, Any] | None) -> str | None:
    if declaration is None:
        return None
    if declaration.get("verification_status") == "verified":
        return str(declaration.get("requested_model"))
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_live_measurements(
    tasks: list[BenchmarkTask],
    *,
    out_dir: Path,
    policy: dict[str, Any] | None = None,
    codex_binary: str = "codex",
    agy_binary: str = "agy",
) -> dict[str, Any]:
    policy = policy or DEFAULT_MODEL_POLICY
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for task in tasks:
        route = resolve_policy_route(
            policy, role_kind=task.role_kind, tier=task.tier
        )
        if route is None:
            results.append(
                {
                    **task.as_dict(),
                    "status": "blocked",
                    "blocked_reason": "ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED",
                }
            )
            continue
        budget = dict(route["budget"])
        task_dir = out_dir / task.task_id
        repo = task_dir / "repo"
        state_root = task_dir / "state"
        evidence_dir = task_dir / "evidence"
        _init_measurement_repo(repo)
        prompt = (
            f"{task.prompt} Create docs/{task.task_id}.txt with exactly: "
            f"{task.task_id} {task.tier} measurement"
        )
        blocked_reason = None
        try:
            result = run_adapter_lane(
                root=str(state_root),
                sandbox=str(repo),
                adapter=str(route["adapter"]),
                task_id=task.task_id,
                prompt=prompt,
                arm="benchmark-measurement",
                tier=task.tier,
                is_supported=lambda _model: True,
                budget=budget,
                predicted_tokens=task.predicted_tokens,
                predicted_usd=task.predicted_usd,
                codex_binary=codex_binary,
                agy_binary=agy_binary,
                evidence_dir=str(evidence_dir),
                allowed_touched_files=["docs/**"],
                model=str(route["model"]),
                write_scope=["docs/**"],
                role_id=task.role_kind,
                role_capability="execute" if task.role_kind == "runner" else "review",
            )
            status = "measured"
        except LaneBlocked as exc:
            result = {}
            status = "blocked"
            blocked_reason = exc.reason

        declaration = _read_json(task_dir / "model-declaration.json")
        actual_model = _actual_model_from_declaration(declaration)
        verification_status = (
            declaration.get("verification_status") if declaration is not None else None
        )
        runner_receipt = result.get("runner_receipt", {}) if result else {}
        results.append(
            {
                **task.as_dict(),
                "status": status,
                "blocked_reason": blocked_reason,
                "route": {"adapter": route["adapter"], "model": route["model"]},
                "budget": budget,
                "budget_compliant": (
                    task.predicted_tokens <= int(budget["max_tokens"])
                    and task.predicted_usd <= float(budget["max_usd"])
                ),
                "declared_model": route["model"],
                "actual_model": actual_model,
                "declared_equals_actual": actual_model == route["model"],
                "model_verification_status": verification_status,
                "degraded": verification_status == "requested-unverified",
                "tokens": None,
                "cost_usd": None,
                "touched_files": list(runner_receipt.get("touched_files", [])),
                "evidence_dir": str(evidence_dir),
            }
        )
    return {
        "kind": MODEL_ROUTING_BENCHMARK_KIND,
        "schema_version": MODEL_ROUTING_BENCHMARK_SCHEMA_VERSION,
        "live": True,
        "summary": {
            "task_count": len(results),
            "measured_count": sum(1 for task in results if task["status"] == "measured"),
            "blocked_count": sum(1 for task in results if task["status"] == "blocked"),
            "degraded_count": sum(1 for task in results if task.get("degraded")),
        },
        "boundary": _live_boundary(),
        "tasks": results,
    }


def select_tasks(
    tasks: list[BenchmarkTask],
    *,
    role_kind: str | None = None,
    tier: str | None = None,
    limit: int | None = None,
) -> list[BenchmarkTask]:
    selected = [
        task
        for task in tasks
        if (role_kind is None or task.role_kind == role_kind)
        and (tier is None or task.tier == tier)
    ]
    if limit is not None:
        return selected[:limit]
    return selected


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Measure static model routing")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--role-kind", choices=("runner", "reviewer"), default=None)
    parser.add_argument("--tier", choices=("quick", "agentic", "frontier"), default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--agy-binary", default="agy")
    args = parser.parse_args(argv)

    tasks = select_tasks(
        default_task_suite(),
        role_kind=args.role_kind,
        tier=args.tier,
        limit=args.limit,
    )
    if args.live:
        out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp())
        payload = run_live_measurements(
            tasks,
            out_dir=out_dir,
            codex_binary=args.codex_binary,
            agy_binary=args.agy_binary,
        )
    else:
        payload = plan_measurements(tasks)
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        write_measurement(Path(args.out), payload)
    print(text, file=sys.stdout)
    return 0
