"""Deterministic ORRO workstyle router v0.

The workstyle decision is advice only. It encodes conservative workflow
judgment without executing commands, calling Depone, or raising assurance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


WORKSTYLE_KIND = "orro-workstyle-decision"
WORKSTYLE_SCHEMA_VERSION = "0.1"

ERR_ORRO_ADVISE_WRITE_FAILED = "ERR_ORRO_ADVISE_WRITE_FAILED"

_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "risky-change",
        "risky change keywords require human review before execution",
        (
            "secret",
            "security",
            "auth",
            "migration",
            "database",
            "delete",
            "rename",
            "public api",
        ),
    ),
    (
        "verification-only",
        "verification keywords should route to proofcheck instead of proofrun",
        ("verify", "verification", "proofcheck", "evidence", "verdict"),
    ),
    (
        "review-only",
        "review keywords should avoid execution unless later evidence is required",
        ("review", "check", "audit", "inspect", "read-only"),
    ),
    (
        "release-readiness",
        "release keywords should prefer readiness and distribution checks",
        ("release", "changelog", "version", "package", "ship"),
    ),
    (
        "trivial-change",
        "trivial wording keywords should keep effort minimal",
        ("typo", "comment", "format", "simple wording"),
    ),
    (
        "docs-change",
        "documentation keywords should use the docs-change profile",
        ("docs", "readme", "spec", "guide", "markdown"),
    ),
]


class OrroWorkstyleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def advise_workstyle(goal: str, *, repo: Path, home: Path | None = None) -> dict[str, Any]:
    goal = goal.strip()
    task_class, rule_matches = _classify(goal)
    profile = _recommended_profile(task_class)
    effort = _recommended_effort(task_class)
    human_review = task_class in {"code-change", "risky-change", "release-readiness"}
    repo = repo.resolve(strict=False)
    resolved_home = home.resolve(strict=False) if home is not None else None

    return {
        "kind": WORKSTYLE_KIND,
        "schema_version": WORKSTYLE_SCHEMA_VERSION,
        "goal": goal,
        "repo": str(repo),
        "home": str(resolved_home) if resolved_home is not None else None,
        "task_class": task_class,
        "recommended_profile": profile,
        "recommended_effort": effort,
        "recommended_path": _recommended_path(goal, repo, resolved_home, task_class, profile),
        "actions_to_skip": _actions_to_skip(task_class),
        "required_gates": _required_gates(task_class),
        "human_review_required": human_review,
        "reasons": _reasons(task_class),
        "rule_matches": rule_matches,
        "boundary": {
            "executes_commands": False,
            "verifies_evidence": False,
            "approves_merge": False,
            "raises_assurance": False,
            "depone_verifies": True,
            "witnessd_executes": True,
            "orro_exposes_workflow": True,
        },
    }


def write_workstyle_decision(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OrroWorkstyleError(ERR_ORRO_ADVISE_WRITE_FAILED, str(exc)) from exc


def _classify(goal: str) -> tuple[str, list[str]]:
    normalized = re.sub(r"\s+", " ", goal.lower())
    for task_class, reason, keywords in _RULES:
        matches = [keyword for keyword in keywords if keyword in normalized]
        if matches:
            return task_class, [f"{task_class}: {reason}: {', '.join(matches)}"]
    if normalized:
        return "code-change", ["code-change: default route for implementation-like goal"]
    return "unknown", ["unknown: no actionable goal text"]


def _recommended_profile(task_class: str) -> str:
    if task_class in {"trivial-change", "docs-change"}:
        return "docs-change"
    if task_class == "review-only":
        return "review-only"
    if task_class == "verification-only":
        return "verification-only"
    if task_class == "release-readiness":
        return "release-readiness"
    return "code-change"


def _recommended_effort(task_class: str) -> str:
    if task_class == "trivial-change":
        return "minimal"
    if task_class in {"risky-change", "unknown"}:
        return "guarded"
    return "bounded"


def _recommended_path(
    goal: str,
    repo: Path,
    home: Path | None,
    task_class: str,
    profile: str,
) -> list[dict[str, Any]]:
    home_args = ["--home", str(home)] if home is not None else []
    if task_class == "review-only":
        return [
            _step(
                "scout",
                ["orro", "scout", goal, "--repo", str(repo)],
                "review-only work should inspect scoped context without running proofrun",
            ),
            _step(
                "flowplan",
                ["orro", "flowplan", goal, "--root", str(repo), "--profile", profile],
                "compile review intent without claiming execution evidence",
            ),
        ]
    if task_class == "verification-only":
        return [
            _step(
                "proofcheck",
                ["orro", "proofcheck", "<run-dir>", *home_args, "--out", "<run-dir>/proofcheck-verdict.json"],
                "verification-only work should delegate evidence interpretation to Depone",
                verifies=True,
            )
        ]
    if task_class == "release-readiness":
        return [
            _step("init", ["orro", "init", *home_args], "confirm provisioning metadata exists"),
            _step("doctor", ["orro", "doctor", *home_args, "--json"], "check readiness without verifying evidence"),
            _step(
                "engine-lock",
                ["orro", "engine-lock", *home_args, "--out", "<home>/orro-engine-lock.json"],
                "record distribution metadata without treating it as proof",
            ),
            _step("next", ["orro", "next", "<run-dir>", *home_args, "--json"], "inspect continuation state before automation"),
        ]
    if task_class == "trivial-change":
        return []
    return [
        _step(
            "scout",
            ["orro", "scout", goal, "--repo", str(repo)],
            "non-trivial source work should inspect repo context first",
        ),
        _step(
            "flowplan",
            ["orro", "flowplan", goal, "--root", str(repo), "--profile", profile],
            "compile workflow intent before execution",
        ),
        _step(
            "proofrun",
            ["orro", "proofrun", goal, "--repo", str(repo), *home_args],
            "execution evidence is required before proofcheck and formal handoff",
            executes=True,
        ),
        _step(
            "proofcheck",
            ["orro", "proofcheck", "<run-dir>", *home_args, "--out", "<run-dir>/proofcheck-verdict.json"],
            "Depone must verify evidence before handoff",
            verifies=True,
        ),
        _step(
            "handoff",
            ["orro", "handoff", "<run-dir>", "--out", "<run-dir>/orro-handoff.json"],
            "handoff packages review context after passing bound proofcheck",
        ),
    ]


def _step(
    phase: str,
    command: list[str],
    reason: str,
    *,
    executes: bool = False,
    verifies: bool = False,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "command": command,
        "reason": reason,
        "executes_workers": executes,
        "verifies_evidence": verifies,
    }


def _actions_to_skip(task_class: str) -> list[dict[str, str]]:
    actions = [
        {
            "action": "unbounded auto",
            "reason": "full autonomous proofrun is not enabled",
        },
        {
            "action": "LLM confidence as proof",
            "reason": "model confidence cannot replace proofcheck",
        },
    ]
    if task_class in {"trivial-change", "review-only", "verification-only"}:
        actions.append(
            {
                "action": "role-lane team execution",
                "reason": "do not spend team execution on this class unless evidence is explicitly required",
            }
        )
    if task_class in {"review-only", "verification-only"}:
        actions.append(
            {
                "action": "proofrun",
                "reason": f"{task_class} should not launch execution",
            }
        )
    if task_class == "risky-change":
        actions.append(
            {
                "action": "auto proofrun",
                "reason": "risky changes require human review and explicit execution gates",
            }
        )
    return actions


def _required_gates(task_class: str) -> list[str]:
    gates = [
        "proofrun evidence before proofcheck",
        "passing bound proofcheck-verdict.json before handoff",
        "workflow plans, role-lane plans, auto plans, and handoff prose are not assurance",
    ]
    if task_class in {"risky-change", "code-change", "release-readiness"}:
        gates.append("human review before merge or assurance claims")
    return gates


def _reasons(task_class: str) -> list[str]:
    return {
        "trivial-change": [
            "goal appears small; make the edit directly without mandatory scout or flowplan"
        ],
        "docs-change": ["goal appears documentation-focused"],
        "code-change": ["goal appears to require source modification", "formal handoff requires proofcheck"],
        "review-only": ["goal appears review-only; execution evidence is not implied"],
        "verification-only": ["goal appears to require evidence verification", "Depone owns proofcheck"],
        "release-readiness": ["goal appears release-oriented; readiness metadata is not assurance"],
        "risky-change": ["goal matches risky-change keywords", "human review is required before execution"],
        "unknown": ["goal is not specific enough for execution recommendation"],
    }.get(task_class, ["default ORRO workstyle route"])
