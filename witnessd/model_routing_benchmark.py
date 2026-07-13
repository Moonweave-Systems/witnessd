"""Opt-in measurement harness for static ORRO model routing.

This module is deliberately outside the proof/assurance path. Offline use only
plans measurements from the static policy; live execution is explicit and emits
measurement JSON, not a benchmark claim or verifier result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.model_policy import DEFAULT_MODEL_POLICY, resolve_policy_route

MODEL_ROUTING_BENCHMARK_KIND = "moonweave-model-routing-measurement"
MODEL_ROUTING_BENCHMARK_SCHEMA_VERSION = "0.2"


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    role_kind: str
    tier: str
    prompt: str
    predicted_tokens: int
    predicted_usd: float
    repo_files: tuple[tuple[str, str], ...] = ()
    expected_verification: dict[str, Any] = field(default_factory=dict)
    expected_touched_files: tuple[str, ...] = ()
    comparative_value: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role_kind": self.role_kind,
            "tier": self.tier,
            "prompt": self.prompt,
            "predicted_tokens": self.predicted_tokens,
            "predicted_usd": self.predicted_usd,
            "goal": self.prompt,
            "repo_state": {
                "files": [
                    {"path": path, "content": content}
                    for path, content in self.repo_files
                ]
            },
            "expected_verification": dict(self.expected_verification),
            "expected_touched_files": list(self.expected_touched_files),
            "comparative_value": dict(self.comparative_value),
        }


def default_task_suite() -> list[BenchmarkTask]:
    """Return representative, locally executable routing tasks.

    Each task carries a complete seed repository and an expected local outcome.
    The suite is planning/measurement input, not a benchmark claim.
    """

    runner_specs = [
        (
            "quick",
            "Correct the release heading typo in README.md from 'Releaze' to 'Release'.",
            "README.md",
            "# Releaze notes\n",
            "# Release notes\n",
        ),
        (
            "quick",
            "Change enabled in config.json from false to true without changing other keys.",
            "config.json",
            '{"enabled": false, "mode": "safe"}\n',
            '{"enabled": true, "mode": "safe"}\n',
        ),
        (
            "quick",
            "Make clamp() return the lower bound when value is below it.",
            "clamp.py",
            "def clamp(value, low, high):\n    return min(value, high)\n",
            "def clamp(value, low, high):\n    return max(low, min(value, high))\n",
        ),
        (
            "quick",
            "Add the missing 2.3.0 bullet to CHANGELOG.md exactly as shown in the goal.",
            "CHANGELOG.md",
            "# Changelog\n\n## 2.2.0\n- Stable release\n",
            "# Changelog\n\n## 2.3.0\n- Routing measurements\n\n## 2.2.0\n- Stable release\n",
        ),
        (
            "agentic",
            "Fix average() so an empty list returns 0 while preserving non-empty behavior.",
            "stats.py",
            "def average(values):\n    return sum(values) / len(values)\n",
            "def average(values):\n    if not values:\n        return 0\n    return sum(values) / len(values)\n",
        ),
        (
            "agentic",
            "Reject an empty name in normalize_name() with ValueError before normalization.",
            "names.py",
            "def normalize_name(name):\n    return name.strip().lower()\n",
            "def normalize_name(name):\n    if not name.strip():\n        raise ValueError(\"name is required\")\n    return name.strip().lower()\n",
        ),
        (
            "agentic",
            "Make write_state() replace the destination atomically using a .tmp sibling.",
            "state.py",
            "from pathlib import Path\n\ndef write_state(path, text):\n    Path(path).write_text(text)\n",
            "from pathlib import Path\n\ndef write_state(path, text):\n    destination = Path(path)\n    temporary = destination.with_suffix(destination.suffix + \".tmp\")\n    temporary.write_text(text)\n    temporary.replace(destination)\n",
        ),
        (
            "agentic",
            "Normalize Windows separators before checking whether a path starts with docs/.",
            "paths.py",
            "def is_doc(path):\n    return path.startswith(\"docs/\")\n",
            "def is_doc(path):\n    normalized = path.replace(\"\\\\\", \"/\")\n    return normalized.startswith(\"docs/\")\n",
        ),
        (
            "frontier",
            "Make visit() detect dependency cycles and raise ValueError('cycle') instead of recursing forever.",
            "graph.py",
            "def visit(node, edges, seen):\n    for child in edges.get(node, []):\n        if child not in seen:\n            visit(child, edges, seen)\n    seen.add(node)\n",
            "def visit(node, edges, seen, active=None):\n    active = set() if active is None else active\n    if node in active:\n        raise ValueError(\"cycle\")\n    active.add(node)\n    for child in edges.get(node, []):\n        if child not in seen:\n            visit(child, edges, seen, active)\n    active.remove(node)\n    seen.add(node)\n",
        ),
        (
            "frontier",
            "Allow only pending->running and running->complete transitions; reject every other transition.",
            "lifecycle.py",
            "def transition(current, target):\n    return target\n",
            "def transition(current, target):\n    allowed = {(\"pending\", \"running\"), (\"running\", \"complete\")}\n    if (current, target) not in allowed:\n        raise ValueError(\"invalid transition\")\n    return target\n",
        ),
        (
            "frontier",
            "Redact token query parameters while preserving all other URL components.",
            "redact.py",
            "from urllib.parse import urlsplit\n\ndef redact(url):\n    return url\n",
            "from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit\n\ndef redact(url):\n    parts = urlsplit(url)\n    query = urlencode([(key, \"REDACTED\" if key == \"token\" else value) for key, value in parse_qsl(parts.query)])\n    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))\n",
        ),
        (
            "frontier",
            "Make apply_once() idempotent by recording and checking operation ids.",
            "operations.py",
            "def apply_once(operation_id, seen, action):\n    action()\n",
            "def apply_once(operation_id, seen, action):\n    if operation_id in seen:\n        return False\n    action()\n    seen.add(operation_id)\n    return True\n",
        ),
    ]
    reviewer_specs = [
        ("quick", "Review this timeout helper for a correctness risk.", "timeout.py", "def timeout(value):\n    return value or 30\n"),
        ("quick", "Review this permission check for a fail-open risk.", "auth.py", "def allowed(user):\n    return user is None or user.is_admin\n"),
        ("quick", "Review this parser for an unhandled input case.", "parse.py", "def parse(line):\n    key, value = line.split(\"=\")\n    return key, value\n"),
        ("quick", "Review this release command for a destructive default.", "release.py", "def command(branch=\"main\"):\n    return [\"git\", \"push\", \"--force\", \"origin\", branch]\n"),
        ("agentic", "Review the cache update for a lost-update race.", "cache.py", "def increment(cache, key):\n    cache[key] = cache.get(key, 0) + 1\n"),
        ("agentic", "Review this migration for backwards-compatibility regressions.", "api.py", "def load(payload):\n    return payload[\"display_name\"]\n"),
        ("agentic", "Review this retry loop for duplicate side effects.", "retry.py", "def send(client, item):\n    for _ in range(3):\n        try:\n            return client.post(item)\n        except TimeoutError:\n            pass\n"),
        ("agentic", "Review this path check for traversal bypasses.", "files.py", "def safe(path):\n    return not path.startswith(\"../\")\n"),
        ("frontier", "Review signature acceptance for trust-boundary failures.", "signatures.py", "def accepted(signature, known):\n    return signature in known or not known\n"),
        ("frontier", "Review this state replay loop for ordering and duplication hazards.", "replay.py", "def replay(events, apply):\n    for event in sorted(events, key=lambda item: item[\"time\"]):\n        apply(event)\n"),
        ("frontier", "Review this tenant query for authorization isolation failures.", "tenant.py", "def records(db, tenant_id):\n    return db.execute(\"select * from records\").fetchall()\n"),
        ("frontier", "Review evidence promotion for an assurance-boundary violation.", "evidence.py", "def verdict(adapter_ok, transcript):\n    return \"pass\" if adapter_ok and transcript else \"blocked\"\n"),
    ]

    tasks: list[BenchmarkTask] = []
    for index, (tier, prompt, path, before, after) in enumerate(runner_specs, 1):
        tasks.append(
            BenchmarkTask(
                task_id=f"mr-{index:02d}",
                role_kind="runner",
                tier=tier,
                prompt=prompt,
                predicted_tokens={"quick": 4000, "agentic": 12000, "frontier": 30000}[tier],
                predicted_usd={"quick": 0.05, "agentic": 0.20, "frontier": 0.60}[tier],
                repo_files=((path, before),),
                expected_verification={"kind": "file_content", "path": path, "content": after},
                expected_touched_files=(path,),
                comparative_value={
                    "primary_role": "runner",
                    "value": "implements and leaves a locally checkable repository change",
                },
            )
        )
    for offset, (tier, prompt, path, content) in enumerate(reviewer_specs, 1):
        tasks.append(
            BenchmarkTask(
                task_id=f"mr-{len(runner_specs) + offset:02d}",
                role_kind="reviewer",
                tier=tier,
                prompt=f"{prompt} Do not edit files. Return concise findings with file and line references.",
                predicted_tokens={"quick": 3000, "agentic": 9000, "frontier": 20000}[tier],
                predicted_usd={"quick": 0.03, "agentic": 0.12, "frontier": 0.35}[tier],
                repo_files=((path, content),),
                expected_verification={"kind": "read_only_review", "path": path},
                expected_touched_files=(),
                comparative_value={
                    "primary_role": "reviewer",
                    "value": "finds risk without mutating the repository or raising assurance",
                },
            )
        )
    return tasks


def measurement_boundary() -> dict[str, bool]:
    return {
        "advisory_only": True,
        "proof": False,
        "assurance": False,
        "verifier_truth": False,
        "benchmark_claim": False,
        "can_change_evidence_verdict": False,
        "live_model_calls": False,
        "fallback_observation_complete": False,
        "multi_candidate_fallback_enabled": False,
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


def _init_measurement_repo(repo: Path, task: BenchmarkTask) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "benchmark@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "benchmark"], cwd=repo, check=True)
    repo_files = task.repo_files or (("README.md", "benchmark fixture\n"),)
    for relative_path, content in repo_files:
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _task_outcome(
    task: BenchmarkTask,
    *,
    repo: Path,
    runner_receipt: dict[str, Any],
    transcript_path: Path,
    review_receipt_path: Path,
) -> bool:
    command_ok = int(runner_receipt.get("exit_code", 1)) == 0
    touched_files = list(runner_receipt.get("touched_files", []))
    expected = task.expected_verification
    kind = expected.get("kind")
    if kind == "file_content":
        target = repo / str(expected.get("path", ""))
        try:
            content_matches = target.read_text(encoding="utf-8") == expected.get("content")
        except OSError:
            content_matches = False
        return (
            command_ok
            and content_matches
            and sorted(touched_files) == sorted(task.expected_touched_files)
        )
    if kind == "read_only_review":
        try:
            transcript_present = bool(transcript_path.read_text(encoding="utf-8").strip())
        except OSError:
            transcript_present = False
        review_receipt = _read_json(review_receipt_path)
        findings = (
            review_receipt.get("findings", [])
            if review_receipt is not None
            and review_receipt.get("kind") == "moonweave-review-receipt"
            else []
        )
        expected_path = str(expected.get("path", ""))
        relevant_finding = any(
            isinstance(finding, dict)
            and finding.get("file") == expected_path
            and isinstance(finding.get("summary"), str)
            and bool(finding["summary"].strip())
            for finding in findings
        )
        return (
            command_ok
            and not touched_files
            and transcript_present
            and relevant_finding
        )
    return False


def _usage_from_transcript(path: Path) -> dict[str, int | None]:
    input_tokens = 0
    output_tokens = 0
    usage_seen = False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        observed_input = usage.get("input_tokens")
        observed_output = usage.get("output_tokens")
        if isinstance(observed_input, int):
            input_tokens += observed_input
            usage_seen = True
        if isinstance(observed_output, int):
            output_tokens += observed_output
            usage_seen = True
    return {
        "input": input_tokens if usage_seen else None,
        "output": output_tokens if usage_seen else None,
    }


def _turn_count(events: list[dict[str, Any]]) -> int:
    turn_ids = {
        str(event["turn_id"])
        for event in events
        if isinstance(event, dict) and event.get("turn_id")
    }
    if turn_ids:
        return len(turn_ids)
    completed = sum(
        1
        for event in events
        if isinstance(event, dict) and event.get("event_type") == "turn.completed"
    )
    return completed or (1 if events else 0)


def _estimated_cost_usd(
    task: BenchmarkTask,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    if input_tokens is None and output_tokens is None:
        return None
    observed_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    if task.predicted_tokens <= 0:
        return None
    return round(task.predicted_usd * observed_tokens / task.predicted_tokens, 6)


def _depone_root() -> Path | None:
    configured = os.environ.get("WITNESSD_DEPONE_ROOT")
    candidates = [
        Path(configured).resolve(strict=False) if configured else None,
        Path(__file__).resolve().parents[2] / "depone",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "depone" / "__main__.py").is_file():
            return candidate
    return None


def _verify_bundle_with_depone(result: dict[str, Any]) -> dict[str, Any]:
    bundle_path = str(result.get("bundle_path", ""))
    public_key_path = str(result.get("public_key_path", ""))
    depone_root = _depone_root()
    if not bundle_path or not public_key_path or depone_root is None:
        return {
            "engine": "depone",
            "command": "agent-fabric-verify-signature",
            "decision": "unavailable",
            "exit_code": None,
            "detail": "bundle, public key, or pinned Depone root unavailable",
        }
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(depone_root)
        if not current_pythonpath
        else f"{depone_root}{os.pathsep}{current_pythonpath}"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "depone",
            "agent-fabric-verify-signature",
            "--bundle",
            bundle_path,
            "--public-key",
            public_key_path,
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return {
        "engine": "depone",
        "command": "agent-fabric-verify-signature",
        "scope": "signed measurement evidence bundle",
        "decision": "pass" if completed.returncode == 0 else "blocked",
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _model_receipts(
    *,
    declared_model: str,
    actual_model: str | None,
    verification_status: str | None,
    blocked_reason: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fallback_receipts: list[dict[str, Any]] = []
    unavailable_receipts: list[dict[str, Any]] = []
    if actual_model is not None and actual_model != declared_model:
        fallback_receipts.append(
            {
                "kind": "model-fallback-receipt",
                "from_model": declared_model,
                "to_model": actual_model,
                "reason": "observed model differs from declared route",
                "silent": False,
            }
        )
    if verification_status == "requested-unverified":
        unavailable_receipts.append(
            {
                "kind": "unavailable-model-receipt",
                "requested_model": declared_model,
                "reason": "adapter cannot report the actual model",
                "fallback_attempted": False,
            }
        )
    elif verification_status == "rejected" or blocked_reason in {
        "model_rejected",
        "preflight_blocked",
    }:
        unavailable_receipts.append(
            {
                "kind": "unavailable-model-receipt",
                "requested_model": declared_model,
                "reason": blocked_reason or "requested model rejected",
                "fallback_attempted": False,
            }
        )
    return fallback_receipts, unavailable_receipts


def build_live_measurement_record(
    task: BenchmarkTask,
    *,
    route: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    """Build one v0.2 live record from injected, already-observed values."""

    budget = dict(route.get("budget", {}))
    input_tokens = execution.get("input_tokens")
    output_tokens = execution.get("output_tokens")
    total_tokens = (
        None
        if input_tokens is None and output_tokens is None
        else int(input_tokens or 0) + int(output_tokens or 0)
    )
    actual_model = execution.get("actual_model")
    declared_model = str(execution.get("declared_model") or route.get("model", ""))
    return {
        **task.as_dict(),
        "status": str(execution.get("status", "blocked")),
        "blocked_reason": execution.get("blocked_reason"),
        "success": bool(execution.get("success", False)),
        "verifier": dict(execution.get("verifier", {})),
        "elapsed_seconds": execution.get("elapsed_seconds"),
        "turn_count": int(execution.get("turn_count", 0)),
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "estimated_cost_usd": execution.get("estimated_cost_usd"),
        "cost_estimate_method": (
            "task_prediction_proportional_to_observed_tokens"
            if execution.get("estimated_cost_usd") is not None
            else "unavailable"
        ),
        "fallback_receipts": list(execution.get("fallback_receipts", [])),
        "unavailable_model_receipts": list(
            execution.get("unavailable_model_receipts", [])
        ),
        "comparative_value": {
            **task.comparative_value,
            "task_success": bool(execution.get("success", False)),
            "verifier_decision": execution.get("verifier", {}).get("decision"),
            "elapsed_seconds": execution.get("elapsed_seconds"),
            "total_tokens": total_tokens,
            "estimated_cost_usd": execution.get("estimated_cost_usd"),
        },
        "route": {
            "adapter": route.get("adapter"),
            "model": route.get("model"),
        },
        "budget": budget,
        "budget_compliant": (
            total_tokens is None or total_tokens <= int(budget.get("max_tokens", 0))
        )
        and (
            execution.get("estimated_cost_usd") is None
            or float(execution["estimated_cost_usd"])
            <= float(budget.get("max_usd", 0.0))
        ),
        "declared_model": declared_model,
        "actual_model": actual_model,
        "declared_equals_actual": (
            None if actual_model is None else actual_model == declared_model
        ),
        "model_verification_status": execution.get("model_verification_status"),
        "model_observation_scope": "cli_declaration_status",
        "degraded": bool(execution.get("degraded", False)),
        "touched_files": list(execution.get("touched_files", [])),
        "evidence_dir": execution.get("evidence_dir"),
    }


def compare_role_value(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize observed runner/reviewer value without ranking or assurance."""

    roles: dict[str, dict[str, Any]] = {}
    for role_kind in ("runner", "reviewer"):
        records = [
            record
            for record in measurements
            if record.get("role_kind") == role_kind
        ]
        elapsed = [
            float(record["elapsed_seconds"])
            for record in records
            if isinstance(record.get("elapsed_seconds"), (int, float))
        ]
        tokens = [
            int(record["tokens"]["total"])
            for record in records
            if isinstance(record.get("tokens"), dict)
            and isinstance(record["tokens"].get("total"), int)
        ]
        costs = [
            float(record["estimated_cost_usd"])
            for record in records
            if isinstance(record.get("estimated_cost_usd"), (int, float))
        ]
        task_count = len(records)
        success_count = sum(1 for record in records if record.get("success"))
        verifier_pass_count = sum(
            1
            for record in records
            if record.get("verifier", {}).get("decision") == "pass"
        )
        roles[role_kind] = {
            "task_count": task_count,
            "success_count": success_count,
            "success_rate": round(success_count / task_count, 6) if task_count else None,
            "verifier_pass_count": verifier_pass_count,
            "verifier_pass_rate": (
                round(verifier_pass_count / task_count, 6) if task_count else None
            ),
            "mean_elapsed_seconds": (
                round(sum(elapsed) / len(elapsed), 6) if elapsed else None
            ),
            "mean_total_tokens": (
                round(sum(tokens) / len(tokens), 3) if tokens else None
            ),
            "mean_estimated_cost_usd": (
                round(sum(costs) / len(costs), 6) if costs else None
            ),
        }
    return {
        "roles": roles,
        "boundary": {
            "advisory_only": True,
            "ranks_models": False,
            "benchmark_claim": False,
            "proof": False,
            "assurance": False,
        },
    }


def derive_tier_budgets(
    measurements: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
    headroom: float = 1.25,
) -> dict[str, Any]:
    """Derive advisory budgets without exceeding current policy ceilings."""

    if headroom < 1.0:
        raise ValueError("headroom must be at least 1.0")
    policy = policy or DEFAULT_MODEL_POLICY
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for measurement in measurements:
        if measurement.get("status") != "measured":
            continue
        key = (str(measurement.get("role_kind")), str(measurement.get("tier")))
        grouped.setdefault(key, []).append(measurement)

    tiers = []
    for (role_kind, tier), records in sorted(grouped.items()):
        route = resolve_policy_route(policy, role_kind=role_kind, tier=tier)
        if route is None:
            continue
        ceiling = dict(route["budget"])
        observed_tokens = [
            int(record["tokens"]["total"])
            for record in records
            if isinstance(record.get("tokens"), dict)
            and isinstance(record["tokens"].get("total"), int)
        ]
        observed_costs = [
            float(record["estimated_cost_usd"])
            for record in records
            if isinstance(record.get("estimated_cost_usd"), (int, float))
        ]
        observed_times = [
            float(record["elapsed_seconds"])
            for record in records
            if isinstance(record.get("elapsed_seconds"), (int, float))
        ]
        proposed_tokens = (
            min(
                int(ceiling["max_tokens"]),
                math.ceil(max(observed_tokens) * headroom),
            )
            if observed_tokens
            else int(ceiling["max_tokens"])
        )
        proposed_cost = (
            min(
                float(ceiling["max_usd"]),
                round(max(observed_costs) * headroom, 6),
            )
            if observed_costs
            else float(ceiling["max_usd"])
        )
        proposed = {
            "max_tokens": proposed_tokens,
            "max_usd": proposed_cost,
            "max_depth": int(ceiling["max_depth"]),
            "max_elapsed_seconds": round(
                max(observed_times, default=0.0) * headroom, 3
            ),
        }
        tiers.append(
            {
                "role_kind": role_kind,
                "tier": tier,
                "sample_count": len(records),
                "measurement_availability": {
                    "tokens": bool(observed_tokens),
                    "cost": bool(observed_costs),
                    "elapsed": bool(observed_times),
                },
                "policy_ceiling": ceiling,
                "proposed_budget": proposed,
                "within_policy_ceiling": (
                    proposed_tokens <= int(ceiling["max_tokens"])
                    and proposed_cost <= float(ceiling["max_usd"])
                    and proposed["max_depth"] <= int(ceiling["max_depth"])
                ),
            }
        )
    return {
        "kind": "moonweave-model-routing-budget-advisory",
        "schema_version": "0.1",
        "headroom": headroom,
        "tiers": tiers,
        "boundary": {
            "advisory_only": True,
            "changes_model_policy": False,
            "proof": False,
            "assurance": False,
            "benchmark_claim": False,
        },
    }


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
    results: list[dict[str, Any]] = []
    for task in tasks:
        route = resolve_policy_route(
            policy, role_kind=task.role_kind, tier=task.tier
        )
        if route is None:
            results.append(
                build_live_measurement_record(
                    task,
                    route={},
                    execution={
                        "status": "blocked",
                        "success": False,
                        "verifier": {
                            "engine": "depone",
                            "decision": "not_run",
                            "reason": "routing policy unresolved",
                        },
                        "elapsed_seconds": 0.0,
                        "turn_count": 0,
                        "input_tokens": None,
                        "output_tokens": None,
                        "estimated_cost_usd": None,
                        "fallback_receipts": [],
                        "unavailable_model_receipts": [],
                        "blocked_reason": "ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED",
                    },
                )
            )
            continue
        budget = dict(route["budget"])
        task_dir = out_dir / task.task_id
        repo = task_dir / "repo"
        state_root = task_dir / "state"
        evidence_dir = task_dir / "evidence"
        _init_measurement_repo(repo, task)
        prompt = task.prompt
        if task.expected_verification.get("kind") == "file_content":
            prompt = (
                f"{prompt} The final UTF-8 file content must be exactly: "
                f"{json.dumps(task.expected_verification.get('content'))}"
            )
        blocked_reason: str | None = None
        started = time.monotonic()
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
                allowed_touched_files=list(task.expected_touched_files),
                model=str(route["model"]),
                write_scope=list(task.expected_touched_files),
                role_id=task.role_kind,
                role_capability="execute" if task.role_kind == "runner" else "review",
            )
            status = "measured"
        except LaneBlocked as exc:
            result = {}
            status = "blocked"
            blocked_reason = exc.reason
        elapsed_seconds = round(time.monotonic() - started, 6)

        declaration = _read_json(task_dir / "model-declaration.json")
        actual_model = _actual_model_from_declaration(declaration)
        verification_status = (
            declaration.get("verification_status") if declaration is not None else None
        )
        runner_receipt = result.get("runner_receipt", {}) if result else {}
        normalized_events = list(result.get("normalized_events", [])) if result else []
        usage = _usage_from_transcript(task_dir / "adapter-transcript.txt")
        estimated_cost = _estimated_cost_usd(
            task,
            input_tokens=usage["input"],
            output_tokens=usage["output"],
        )
        fallback_receipts, unavailable_receipts = _model_receipts(
            declared_model=str(route["model"]),
            actual_model=actual_model,
            verification_status=(
                str(verification_status) if verification_status is not None else None
            ),
            blocked_reason=blocked_reason,
        )
        verifier = (
            _verify_bundle_with_depone(result)
            if result
            else {
                "engine": "depone",
                "command": "agent-fabric-verify-signature",
                "decision": "not_run",
                "reason": blocked_reason or "adapter lane did not emit evidence",
            }
        )
        results.append(
            build_live_measurement_record(
                task,
                route=route,
                execution={
                    "status": status,
                    "blocked_reason": blocked_reason,
                    "success": (
                        _task_outcome(
                            task,
                            repo=repo,
                            runner_receipt=runner_receipt,
                            transcript_path=task_dir / "adapter-transcript.txt",
                            review_receipt_path=task_dir / "review-receipt.json",
                        )
                        if result
                        else False
                    ),
                    "verifier": verifier,
                    "elapsed_seconds": elapsed_seconds,
                    "turn_count": _turn_count(normalized_events),
                    "input_tokens": usage["input"],
                    "output_tokens": usage["output"],
                    "estimated_cost_usd": estimated_cost,
                    "fallback_receipts": fallback_receipts,
                    "unavailable_model_receipts": unavailable_receipts,
                    "declared_model": route["model"],
                    "actual_model": actual_model,
                    "model_verification_status": verification_status,
                    "degraded": verification_status == "requested-unverified",
                    "touched_files": list(runner_receipt.get("touched_files", [])),
                    "evidence_dir": str(evidence_dir),
                },
            )
        )
    payload = {
        "kind": MODEL_ROUTING_BENCHMARK_KIND,
        "schema_version": MODEL_ROUTING_BENCHMARK_SCHEMA_VERSION,
        "live": True,
        "summary": {
            "task_count": len(results),
            "measured_count": sum(1 for task in results if task["status"] == "measured"),
            "blocked_count": sum(1 for task in results if task["status"] == "blocked"),
            "degraded_count": sum(1 for task in results if task.get("degraded")),
            "success_count": sum(1 for task in results if task.get("success")),
            "verifier_pass_count": sum(
                1
                for task in results
                if task.get("verifier", {}).get("decision") == "pass"
            ),
            "fallback_count": sum(
                len(task.get("fallback_receipts", [])) for task in results
            ),
        },
        "boundary": _live_boundary(),
        "tasks": results,
    }
    payload["budget_advisory"] = derive_tier_budgets(results, policy=policy)
    payload["comparative_role_value"] = compare_role_value(results)
    return payload


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
