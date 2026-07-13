"""Deterministic ORRO ideation and root-cause advisory surfaces.

Sketch and trace are planning context only. They inspect repository shape but
do not execute commands, mutate the repository, run workers, call Depone, or
change an evidence verdict.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


ADVISORY_SCHEMA_VERSION = "0.1"
ERR_ORRO_ADVISORY_WRITE_FAILED = "ERR_ORRO_ADVISORY_WRITE_FAILED"
ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO = "ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO"

_IGNORED_DIRS = {".git", ".witnessd", ".omx", "__pycache__", "build", "dist"}
_LANGUAGE_SUFFIXES = {
    ".c": "C",
    ".cpp": "C++",
    ".go": "Go",
    ".java": "Java",
    ".js": "JavaScript",
    ".kt": "Kotlin",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".swift": "Swift",
    ".ts": "TypeScript",
}


class OrroAdvisoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_sketch_decision(goal: str, *, repo: Path, home: Path | None = None) -> dict[str, Any]:
    """Frame, diverge, and converge without claiming implementation evidence."""

    normalized_goal = _normalize_text(goal)
    repo = repo.resolve(strict=False)
    resolved_home = home.resolve(strict=False) if home is not None else None
    signals = _repo_signals(repo, normalized_goal)
    constraints = _extract_constraints(normalized_goal)
    candidates = _sketch_candidates(signals)
    chosen = candidates[0]
    branches = _decision_branches(signals)
    flowplan_input = _flowplan_input(normalized_goal, chosen, constraints, branches)

    return {
        "kind": "orro-sketch",
        "schema_version": ADVISORY_SCHEMA_VERSION,
        "goal": normalized_goal,
        "repo": str(repo),
        "home": str(resolved_home) if resolved_home is not None else None,
        "method": {
            "sequence": ["frame", "diverge", "converge", "resolve-branches", "handoff"],
            "rule": "compare distinct approaches before selecting one direction",
        },
        "problem_frame": {
            "desired_outcome": normalized_goal,
            "constraints": constraints,
            "repo_signals": signals,
            "success_criteria": [
                "one direction is selected with explicit rationale",
                "every material decision branch carries one recommended answer",
                "the selected direction is shaped as flowplan input",
            ],
        },
        "candidate_approaches": candidates,
        "chosen_direction": {
            "approach_id": chosen["id"],
            "summary": chosen["summary"],
            "rationale": chosen["selection_rationale"],
            "flowplan_input": flowplan_input,
        },
        "decision_branches": branches,
        "flowplan_handoff": {
            "kind": "orro-flowplan-input",
            "goal": flowplan_input,
            "profile": "code-change",
            "source_kind": "orro-sketch",
            "command": [
                "orro",
                "flowplan",
                flowplan_input,
                "--root",
                str(repo),
                "--profile",
                "code-change",
            ],
            "is_evidence": False,
        },
        "boundary": _advisory_boundary(),
        "status_note": _status_note(),
    }


def build_trace_decision(
    symptom: str,
    *,
    repo: Path,
    home: Path | None = None,
) -> dict[str, Any]:
    """Build a root-cause-first investigation record without proposing an ungrounded fix."""

    normalized_symptom = _normalize_text(symptom)
    repo = repo.resolve(strict=False)
    resolved_home = home.resolve(strict=False) if home is not None else None
    signals = _repo_signals(repo, normalized_symptom)
    evidence = _trace_evidence(repo, signals)
    hypotheses = _ranked_hypotheses(signals)
    localized = bool(signals["matching_paths"])
    reproduction_status = "localized-not-reproduced" if localized else "not-reproduced"

    return {
        "kind": "orro-trace",
        "schema_version": ADVISORY_SCHEMA_VERSION,
        "goal_or_symptom": normalized_symptom,
        "symptom": normalized_symptom,
        "repo": str(repo),
        "home": str(resolved_home) if resolved_home is not None else None,
        "method": {
            "sequence": ["observe", "reproduce-localize", "hypothesize", "confirm-root-cause"],
            "gate": "a fix may be shaped only after root cause is confirmed",
        },
        "reproduction": {
            "status": reproduction_status,
            "steps": [],
            "localization_candidates": signals["matching_paths"],
            "next_read_only_action": (
                "run the smallest operator-approved reproduction and capture exact output"
            ),
            "reason": (
                "this advisory surface accepts no arbitrary command, so it does not invent or "
                "execute a reproduction"
            ),
        },
        "evidence_gathered": evidence,
        "ranked_hypotheses": hypotheses,
        "root_cause": {
            "status": "unconfirmed",
            "finding": None,
            "confirmation_evidence": [],
            "blocked_by": [
                "no repeatable reproduction has been observed",
                "no hypothesis has passed its confirmation test",
            ],
        },
        "investigation_phases": [
            {
                "name": "observe",
                "status": "complete",
                "result": "symptom and repository shape recorded",
            },
            {
                "name": "reproduce-localize",
                "status": "partial" if localized else "blocked",
                "result": (
                    "candidate paths localized; reproduction still required"
                    if localized
                    else "no candidate path or repeatable reproduction yet"
                ),
            },
            {
                "name": "hypothesize",
                "status": "complete",
                "result": "ranked hypotheses include disconfirming evidence and one next test each",
            },
            {
                "name": "confirm-root-cause",
                "status": "blocked",
                "result": "root cause remains unconfirmed; fix proposal is gated",
            },
        ],
        "recommended_fix_scope": {
            "fix_proposal_allowed": False,
            "allowed_paths": [],
            "instruction": "do not edit the repository until one hypothesis is confirmed",
            "after_confirmation": (
                "limit the fix to the confirmed source and add the smallest failing reproduction "
                "test before implementation"
            ),
        },
        "flowplan_handoff": {
            "kind": "orro-flowplan-input",
            "status": "blocked-root-cause-unconfirmed",
            "profile": "code-change",
            "source_kind": "orro-trace",
            "goal_template": (
                f"Fix the confirmed root cause of: {normalized_symptom}. "
                "Scope changes to <confirmed-source> and preserve the reproduction as a regression test."
            ),
            "proofrun_after_flowplan": True,
            "is_evidence": False,
        },
        "boundary": _advisory_boundary(),
        "status_note": _status_note(),
    }


def write_advisory_decision(path: Path, payload: dict[str, Any]) -> None:
    resolved_path = path.resolve(strict=False)
    repo = Path(str(payload["repo"])).resolve(strict=False)
    if resolved_path == repo or repo in resolved_path.parents:
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO,
            f"advisory output must be outside the inspected repository: {resolved_path}",
        )
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OrroAdvisoryError(ERR_ORRO_ADVISORY_WRITE_FAILED, str(exc)) from exc


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _repo_signals(repo: Path, text: str) -> dict[str, Any]:
    files = _repo_files(repo)
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_.-]+", text)
        if len(token) >= 3
    }
    matching_paths = [
        path
        for path in files
        if any(token in path.lower() for token in tokens)
    ][:12]
    language_counts = Counter(
        _LANGUAGE_SUFFIXES[Path(path).suffix.lower()]
        for path in files
        if Path(path).suffix.lower() in _LANGUAGE_SUFFIXES
    )
    advisory_patterns = [
        path
        for path in files
        if Path(path).name in {"orro_workstyle.py", "orro_report.py", "SKILL.md", "AGENTS.md"}
    ][:8]
    test_paths = [path for path in files if path.startswith("tests/") or "/tests/" in path][:8]
    return {
        "repo_exists": repo.is_dir(),
        "file_count": len(files),
        "languages": [name for name, _count in language_counts.most_common(5)],
        "matching_paths": matching_paths,
        "existing_advisory_patterns": advisory_patterns,
        "test_paths": test_paths,
    }


def _repo_files(repo: Path) -> list[str]:
    if not repo.is_dir():
        return []
    files: list[str] = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS]
        root_path = Path(root)
        for name in names:
            path = root_path / name
            try:
                if path.is_file():
                    files.append(path.relative_to(repo).as_posix())
            except OSError:
                continue
        if len(files) >= 2000:
            break
    return sorted(files)[:2000]


def _extract_constraints(goal: str) -> list[str]:
    clauses = [part.strip(" .") for part in re.split(r"[.;]", goal) if part.strip()]
    markers = ("without", "must", "do not", "don't", "preserve", "avoid", "only")
    constraints = [clause for clause in clauses if any(marker in clause.lower() for marker in markers)]
    if not constraints:
        constraints = [
            "preserve existing public behavior unless the goal explicitly changes it",
            "prefer the smallest reversible slice that can be verified",
        ]
    return constraints[:6]


def _sketch_candidates(signals: dict[str, Any]) -> list[dict[str, Any]]:
    has_existing_seam = bool(signals["existing_advisory_patterns"])
    first_summary = (
        "extend the nearest existing advisory seam with a bounded additive path"
        if has_existing_seam
        else "add the smallest isolated feature slice beside the nearest existing pattern"
    )
    return [
        {
            "id": "bounded-existing-seam",
            "summary": first_summary,
            "shape": "reuse current CLI, artifact, and test conventions; add only the missing behavior",
            "benefits": ["small diff", "existing conventions stay authoritative", "easy rollback"],
            "risks": ["the existing seam may expose hidden coupling that tests must lock"],
            "tradeoffs": [
                "less architectural freedom in exchange for lower regression and integration risk"
            ],
            "selection_rationale": (
                "repository signals show reusable advisory or guidance seams"
                if has_existing_seam
                else "a bounded adjacent slice minimizes assumptions while repository context is limited"
            ),
        },
        {
            "id": "isolated-module-adapter",
            "summary": "introduce a focused module behind the existing public entrypoint",
            "shape": "separate decision construction from CLI and persistence while retaining current aliases",
            "benefits": ["clear unit boundary", "direct tests", "future internal evolution"],
            "risks": ["one additional module and integration seam"],
            "tradeoffs": ["cleaner isolation in exchange for slightly more structure"],
            "selection_rationale": "use when the existing entrypoint is already crowded or responsibilities differ",
        },
        {
            "id": "new-parallel-subsystem",
            "summary": "create a new subsystem with its own orchestration and artifact lifecycle",
            "shape": "separate parser, state, and workflow ownership",
            "benefits": ["maximum independence"],
            "risks": ["duplicate lifecycle", "larger public surface", "higher maintenance cost"],
            "tradeoffs": ["more autonomy in exchange for duplication and migration risk"],
            "selection_rationale": "reserve for evidence that existing seams cannot carry the behavior safely",
        },
    ]


def _decision_branches(signals: dict[str, Any]) -> list[dict[str, Any]]:
    test_answer = (
        f"extend focused coverage near {signals['test_paths'][0]}"
        if signals["test_paths"]
        else "add a focused regression test beside the new boundary"
    )
    return [
        {
            "id": "scope-boundary",
            "question": "How broad should the first implementation slice be?",
            "status": "recommendation-pending-operator-confirmation",
            "recommended_answer": "ship the smallest reversible end-to-end slice",
            "rationale": "it proves the path without committing to speculative follow-on work",
        },
        {
            "id": "compatibility",
            "question": "Should the new path replace or coexist with current behavior?",
            "status": "recommendation-pending-operator-confirmation",
            "recommended_answer": "add the new path and preserve current behavior",
            "rationale": "additive compatibility keeps rollback and comparison possible",
        },
        {
            "id": "verification-shape",
            "question": "What should lock the selected direction before implementation?",
            "status": "recommendation-pending-operator-confirmation",
            "recommended_answer": test_answer,
            "rationale": "a failing focused test makes the intended behavior observable before code changes",
        },
    ]


def _flowplan_input(
    goal: str,
    chosen: dict[str, Any],
    constraints: list[str],
    branches: list[dict[str, Any]],
) -> str:
    answers = "; ".join(str(branch["recommended_answer"]) for branch in branches)
    return (
        f"{goal}. Chosen direction: {chosen['summary']}. "
        f"Constraints: {'; '.join(constraints)}. Recommended branch resolutions: {answers}."
    )


def _trace_evidence(repo: Path, signals: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": "repository-path",
            "observation": f"repository exists: {repo.is_dir()}",
            "interpretation": "establishes whether local inspection can proceed",
        },
        {
            "source": "read-only-file-inventory",
            "observation": f"observed {signals['file_count']} files and languages {signals['languages']}",
            "interpretation": "bounds the implementation environment without executing it",
        },
        {
            "source": "symptom-token-localization",
            "observation": f"candidate paths: {signals['matching_paths']}",
            "interpretation": "narrows inspection targets but does not confirm causality",
        },
        {
            "source": "reproduction-gate",
            "observation": "no reproduction command was accepted or executed",
            "interpretation": "root cause and fix scope must remain unconfirmed",
        },
    ]


def _ranked_hypotheses(signals: dict[str, Any]) -> list[dict[str, Any]]:
    matches = signals["matching_paths"]
    hypotheses = [
        {
            "hypothesis": "configuration or environment changes the observed behavior",
            "basis": "runtime inputs can diverge from repository defaults",
            "evidence_for": [],
            "evidence_against": ["no environment or configuration observation was supplied"],
            "confirmation_test": "capture effective configuration at the failing boundary and compare it with a working run",
            "status": "unconfirmed",
        },
        {
            "hypothesis": "a recent integration change introduced the symptom",
            "basis": "regressions often appear at changed component boundaries",
            "evidence_for": [],
            "evidence_against": ["this advisory surface did not inspect git history or execute a bisect"],
            "confirmation_test": "compare the smallest known-good and known-bad revisions around the localized path",
            "status": "unconfirmed",
        },
    ]
    if matches:
        hypotheses.insert(
            0,
            {
                "hypothesis": "the symptom originates in the localized implementation path",
                "basis": ", ".join(matches[:3]),
                "evidence_for": matches,
                "evidence_against": ["no repeatable reproduction or data-flow trace yet"],
                "confirmation_test": "reproduce once, then trace the incorrect value backward through the localized call path",
                "status": "unconfirmed",
            },
        )
    else:
        hypotheses.insert(
            0,
            {
                "hypothesis": "repository localization is insufficient to rank a code-path cause",
                "basis": "no symptom token matched an observed repository path",
                "evidence_for": [],
                "evidence_against": ["no candidate implementation path has been localized"],
                "confirmation_test": "capture a repeatable reproduction and trace its first incorrect boundary",
                "status": "localization-required",
            },
        )
    return [dict(item, rank=rank) for rank, item in enumerate(hypotheses, start=1)]


def _advisory_boundary() -> dict[str, bool]:
    return {
        "advisory_only": True,
        "is_evidence": False,
        "raises_assurance": False,
        "verifies_evidence": False,
        "can_change_evidence_verdict": False,
        "executes_proofrun": False,
        "executes_commands": False,
        "runs_workers": False,
        "calls_depone": False,
        "mutates_repo": False,
        "approves_merge": False,
    }


def _status_note() -> str:
    return (
        "Advisory only: not proof, verifier truth, approval, or assurance; "
        "cannot change an evidence verdict."
    )
