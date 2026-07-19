"""Validate and seal ORRO ideation and root-cause advisory records.

Sketch and trace are planning context only. Trace consumes a symptom-bound
prior-run receipt as its external oracle and performs read-only inspection.
Neither surface executes repository code, mutates the inspected repository,
runs workers or proofrun, calls Depone, or changes an evidence verdict.

Calling agents author decisions. The deterministic heuristic path remains only
as an explicitly degraded scaffold for headless compatibility.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from witnessd.canonical import canonical_hash


ORRO_ADVISORY_DECISION_SCHEMA_VERSION = "0.2"
ERR_ORRO_ADVISORY_WRITE_FAILED = "ERR_ORRO_ADVISORY_WRITE_FAILED"
ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO = "ERR_ORRO_ADVISORY_OUTPUT_INSIDE_REPO"
ERR_ORRO_ADVISORY_DECISION_INVALID = "ERR_ORRO_ADVISORY_DECISION_INVALID"
ERR_ORRO_ADVISORY_DECISION_READ_FAILED = "ERR_ORRO_ADVISORY_DECISION_READ_FAILED"
ERR_ORRO_SKETCH_CHOSEN_NOT_IN_CANDIDATES = (
    "ERR_ORRO_SKETCH_CHOSEN_NOT_IN_CANDIDATES"
)
ERR_ORRO_SKETCH_REJECTED_REASON_MISSING = (
    "ERR_ORRO_SKETCH_REJECTED_REASON_MISSING"
)
ERR_ORRO_TRACE_CONFIRMED_UNBACKED = "ERR_ORRO_TRACE_CONFIRMED_UNBACKED"
ERR_ORRO_TRACE_RECEIPT_INVALID = "ERR_ORRO_TRACE_RECEIPT_INVALID"
ERR_ORRO_TRACE_RECEIPT_HASH_MISMATCH = "ERR_ORRO_TRACE_RECEIPT_HASH_MISMATCH"

# `.worktrees` contains non-current checkouts excluded from repository signals.
_IGNORED_DIRS = {
    ".git",
    ".witnessd",
    ".omx",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
}
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


def read_agent_decision(path: Path) -> dict[str, Any]:
    """Read a bounded agent-authored decision object without repairing it."""

    actionable_path_message = (
        "--decision expects a path to a JSON file, not inline text. Provide a file "
        "whose JSON has: frame, candidates[{axis, summary, benefits, risks, "
        "tradeoff}]. Got a value that is not a readable file."
    )
    try:
        resolved_path = path.resolve(strict=False)
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise OrroAdvisoryError(
                ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
                actionable_path_message,
            ) from exc
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
            f"cannot resolve agent-authored decision {path}: {exc}",
        ) from exc
    if not resolved_path.exists():
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
            actionable_path_message,
        )
    try:
        if resolved_path.stat().st_size > 262_144:
            raise OrroAdvisoryError(
                ERR_ORRO_ADVISORY_DECISION_INVALID,
                "agent-authored decision exceeds the 256 KiB read limit",
            )
        value = json.loads(resolved_path.read_text(encoding="utf-8"))
    except OrroAdvisoryError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise OrroAdvisoryError(
                ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
                actionable_path_message,
            ) from exc
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
            f"cannot read agent-authored decision {resolved_path}: {exc}",
        ) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_DECISION_READ_FAILED,
            f"cannot read agent-authored decision {resolved_path}: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise OrroAdvisoryError(
            ERR_ORRO_ADVISORY_DECISION_INVALID,
            "agent-authored decision must be a JSON object",
        )
    return value


def _agent_sketch_decision(
    goal: str,
    *,
    repo: Path,
    home: Path | None,
    decision: dict[str, Any],
) -> dict[str, Any]:
    _require_present_frame(decision.get("frame"), field="frame")
    criteria = decision.get("criteria")
    if criteria is not None and not isinstance(criteria, list):
        _invalid_decision("criteria must be a JSON array when supplied")

    candidates = decision.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        _invalid_decision("candidates must be a non-empty JSON array")
    candidate_axes: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            _invalid_decision(f"candidates[{index}] must be a JSON object")
        axis = _require_text(candidate.get("axis"), field=f"candidates[{index}].axis")
        _require_text(candidate.get("summary"), field=f"candidates[{index}].summary")
        _require_text_list(
            candidate.get("benefits"), field=f"candidates[{index}].benefits"
        )
        _require_text_list(candidate.get("risks"), field=f"candidates[{index}].risks")
        tradeoff = candidate.get("tradeoff")
        tradeoffs = candidate.get("tradeoffs")
        if tradeoff is None and tradeoffs is None:
            _invalid_decision(
                f"candidates[{index}] requires non-empty tradeoff or tradeoffs"
            )
        if tradeoff is not None:
            _require_text(tradeoff, field=f"candidates[{index}].tradeoff")
        if tradeoffs is not None:
            _require_text_list(tradeoffs, field=f"candidates[{index}].tradeoffs")
        if axis in candidate_axes:
            _invalid_decision(f"candidates[{index}].axis duplicates {axis!r}")
        candidate_axes.add(axis)

    chosen = decision.get("chosen")
    if not isinstance(chosen, dict):
        _invalid_decision("chosen must be a JSON object")
    chosen_direction = _require_text(chosen.get("direction"), field="chosen.direction")
    for field in ("reason", "confidence", "what_would_change_it"):
        _require_text(chosen.get(field), field=f"chosen.{field}")
    if chosen_direction not in candidate_axes:
        raise OrroAdvisoryError(
            ERR_ORRO_SKETCH_CHOSEN_NOT_IN_CANDIDATES,
            "chosen.direction must exactly match one candidates[].axis value",
        )

    rejected = decision.get("rejected")
    if not isinstance(rejected, list):
        _invalid_decision("rejected must be a JSON array")
    for index, item in enumerate(rejected):
        if not isinstance(item, dict):
            _invalid_decision(f"rejected[{index}] must be a JSON object")
        _require_text(item.get("option"), field=f"rejected[{index}].option")
        try:
            _require_text(item.get("why_lost"), field=f"rejected[{index}].why_lost")
        except OrroAdvisoryError as exc:
            raise OrroAdvisoryError(
                ERR_ORRO_SKETCH_REJECTED_REASON_MISSING,
                str(exc),
            ) from exc
    _require_text_list(decision.get("no_gos"), field="no_gos", allow_empty=True)
    _require_text_list(
        decision.get("rabbit_holes"), field="rabbit_holes", allow_empty=True
    )

    payload = copy.deepcopy(decision)
    payload.update(
        {
            "kind": "orro-sketch",
            "schema_version": ORRO_ADVISORY_DECISION_SCHEMA_VERSION,
            "goal": goal,
            "repo": str(repo),
            "home": str(home) if home is not None else None,
            "authored_by": "agent",
            "agent_authored": True,
            "degraded": False,
            "boundary": _advisory_boundary(),
            "status_note": _status_note(),
        }
    )
    return payload


def _agent_trace_decision(
    symptom: str,
    *,
    repo: Path,
    home: Path | None,
    decision: dict[str, Any],
) -> dict[str, Any]:
    from witnessd.advisory_provenance import (
        TRACE_RECEIPT,
        load_trace_execution_subject,
        load_trace_reproduction_subject,
    )

    if not isinstance(decision.get("check_the_plug"), dict):
        _invalid_decision("check_the_plug must be a JSON object")
    reproduction_reference = decision.get("reproduction")
    if not isinstance(reproduction_reference, dict):
        _invalid_decision("reproduction must be a JSON object")
    receipt_path = reproduction_reference.get(
        "path",
        reproduction_reference.get(
            "receipt_path", reproduction_reference.get("source")
        ),
    )
    receipt_sha256 = reproduction_reference.get(
        "sha256", reproduction_reference.get("receipt_sha256")
    )
    if receipt_path != TRACE_RECEIPT:
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_RECEIPT_INVALID,
            f"reproduction must reference {TRACE_RECEIPT!r} inside the inspected repository",
        )
    if not isinstance(receipt_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", receipt_sha256
    ):
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_RECEIPT_INVALID,
            "reproduction requires a lowercase canonical receipt sha256",
        )

    hypotheses = decision.get("hypotheses")
    if not isinstance(hypotheses, list):
        _invalid_decision("hypotheses must be a JSON array")
    for index, hypothesis in enumerate(hypotheses):
        if not isinstance(hypothesis, dict):
            _invalid_decision(f"hypotheses[{index}] must be a JSON object")
        for field in ("mechanism", "prediction", "discriminating_probe", "confidence"):
            _require_text(hypothesis.get(field), field=f"hypotheses[{index}].{field}")
    if not isinstance(decision.get("confirmation"), dict):
        _invalid_decision("confirmation must be a JSON object")
    if not isinstance(decision.get("fix_scope"), dict):
        _invalid_decision("fix_scope must be a JSON object")
    if not isinstance(decision.get("localization"), (dict, list, str)):
        _invalid_decision(
            "localization must be a JSON object, array, or non-empty string"
        )
    if isinstance(decision.get("localization"), str):
        _require_text(decision["localization"], field="localization")

    root_cause = decision.get("root_cause")
    unconfirmed = decision.get("unconfirmed")
    if (root_cause is None) == (unconfirmed is None):
        _invalid_decision("trace requires exactly one of root_cause or unconfirmed")
    claimed_tier: str | None = None
    if root_cause is not None:
        if not isinstance(root_cause, dict):
            _invalid_decision("root_cause must be a JSON object")
        claimed_tier = _require_text(root_cause.get("tier"), field="root_cause.tier")
        if claimed_tier not in {"confirmed", "suspected", "speculative"}:
            _invalid_decision(
                "root_cause.tier must be confirmed, suspected, or speculative"
            )
        if not hypotheses:
            _invalid_decision("root_cause requires at least one agent-authored hypothesis")
    elif isinstance(unconfirmed, str):
        _require_text(unconfirmed, field="unconfirmed")
    elif not isinstance(unconfirmed, dict):
        _invalid_decision("unconfirmed must be a JSON object or non-empty string")

    sealed_receipt = load_trace_reproduction_subject(repo)
    if sealed_receipt is None:
        code = (
            ERR_ORRO_TRACE_CONFIRMED_UNBACKED
            if claimed_tier == "confirmed"
            else ERR_ORRO_TRACE_RECEIPT_INVALID
        )
        raise OrroAdvisoryError(
            code,
            f"cannot validate the agent-authored trace: {TRACE_RECEIPT} is missing or malformed",
        )
    actual_receipt_sha256 = canonical_hash(sealed_receipt)
    try:
        source_receipt_sha256 = hashlib.sha256(
            (repo / TRACE_RECEIPT).read_bytes()
        ).hexdigest()
    except OSError as exc:
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_RECEIPT_INVALID,
            f"cannot hash {TRACE_RECEIPT}: {exc}",
        ) from exc
    if receipt_sha256 not in {actual_receipt_sha256, source_receipt_sha256}:
        code = (
            ERR_ORRO_TRACE_CONFIRMED_UNBACKED
            if claimed_tier == "confirmed"
            else ERR_ORRO_TRACE_RECEIPT_HASH_MISMATCH
        )
        raise OrroAdvisoryError(
            code,
            "agent-authored reproduction hash does not match the receipt that would be sealed",
        )

    observed_reproduction = _observed_reproduction(repo, symptom)
    observed_reproduction["symptom"] = symptom
    observed_reproduction["receipt_path"] = TRACE_RECEIPT
    observed_reproduction["receipt_sha256"] = actual_receipt_sha256
    if claimed_tier == "confirmed":
        execution_receipt = load_trace_execution_subject(repo)
        if execution_receipt is None:
            raise OrroAdvisoryError(
                ERR_ORRO_TRACE_CONFIRMED_UNBACKED,
                "confirmed trace requires a real failing command and transcript",
            )
        observed_reproduction["execution_receipt_sha256"] = canonical_hash(
            execution_receipt
        )
        _gate_agent_confirmed_trace(
            symptom,
            hypotheses=hypotheses,
            localization=decision["localization"],
            confirmation=decision["confirmation"],
            root_cause=root_cause,
            reproduction=observed_reproduction,
            receipt=execution_receipt,
        )

    payload = copy.deepcopy(decision)
    payload.update(
        {
            "kind": "orro-trace",
            "schema_version": ORRO_ADVISORY_DECISION_SCHEMA_VERSION,
            "goal_or_symptom": symptom,
            "symptom": symptom,
            "repo": str(repo),
            "home": str(home) if home is not None else None,
            "authored_by": "agent",
            "agent_authored": True,
            "degraded": False,
            "reproduction": observed_reproduction,
            "boundary": _advisory_boundary(),
            "status_note": _status_note(),
        }
    )
    return payload


def _gate_agent_confirmed_trace(
    symptom: str,
    *,
    hypotheses: list[dict[str, Any]],
    localization: Any,
    confirmation: dict[str, Any],
    root_cause: dict[str, Any],
    reproduction: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    hypothesis_index = root_cause.get("hypothesis_index")
    if not (
        isinstance(hypothesis_index, int)
        and not isinstance(hypothesis_index, bool)
        and 0 <= hypothesis_index < len(hypotheses)
    ):
        finding = root_cause.get("finding", root_cause.get("summary"))
        hypothesis_index = next(
            (
                index
                for index, hypothesis in enumerate(hypotheses)
                if hypothesis.get("mechanism") == finding
            ),
            None,
        )
    ruled_out = confirmation.get("rival_hypotheses_ruled_out")
    valid_rivals = (
        [
            index
            for index in ruled_out
            if isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(hypotheses)
            and index != hypothesis_index
        ]
        if isinstance(ruled_out, list)
        else []
    )
    if hypothesis_index is None or not valid_rivals:
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_CONFIRMED_UNBACKED,
            "confirmed trace requires an agent-selected hypothesis and an actively ruled-out rival",
        )

    hypothesis = hypotheses[hypothesis_index]
    probe = hypothesis["discriminating_probe"]
    command = receipt.get("command")
    exit_code = receipt.get("exit_code")
    output = receipt.get("transcript")
    transcript_sha256 = receipt.get("transcript_sha256")
    if (
        reproduction.get("red_observed") is not True
        or not isinstance(command, str)
        or not command.strip()
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code == 0
        or not isinstance(output, str)
        or not output
        or not isinstance(transcript_sha256, str)
        or transcript_sha256
        != hashlib.sha256(output.encode("utf-8")).hexdigest()
        or symptom not in output
        or probe not in output
    ):
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_CONFIRMED_UNBACKED,
            "confirmed trace requires a symptom-bound executed-red receipt containing the selected discriminating probe",
        )

    gate_hypotheses = [
        {**copy.deepcopy(item), "id": f"H{index}"}
        for index, item in enumerate(hypotheses)
    ]
    gate_confirmation = {
        "supported_hypotheses": [f"H{hypothesis_index}"],
        "ruled_out_hypotheses": [f"H{index}" for index in valid_rivals],
        "ruled_out_rival": True,
    }
    suspect_regions = (
        localization.get("suspect_region_cited", [])
        if isinstance(localization, dict)
        else []
    )
    verdict = _trace_verdict(
        symptom,
        reproduction,
        {"suspect_region_cited": suspect_regions},
        gate_hypotheses,
        gate_confirmation,
    )
    if verdict.get("root_cause", {}).get("tier") != "confirmed":
        raise OrroAdvisoryError(
            ERR_ORRO_TRACE_CONFIRMED_UNBACKED,
            "confirmed trace did not pass the external receipt and rival-rejection gate",
        )


def _require_present_frame(value: Any, *, field: str) -> None:
    if isinstance(value, str):
        _require_text(value, field=field)
        return
    if isinstance(value, dict) and value:
        return
    _invalid_decision(f"{field} must be a non-empty string or JSON object")


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid_decision(f"{field} must be a non-empty string")
    return value


def _require_text_list(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        qualifier = (
            "an array of strings" if allow_empty else "a non-empty array of strings"
        )
        _invalid_decision(f"{field} must be {qualifier}")
    return value


def _invalid_decision(message: str) -> None:
    raise OrroAdvisoryError(ERR_ORRO_ADVISORY_DECISION_INVALID, message)


def build_sketch_decision(
    goal: str,
    *,
    repo: Path,
    home: Path | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an agent-authored sketch or emit a degraded scaffold."""

    normalized_goal = _normalize_text(goal)
    repo = repo.resolve(strict=False)
    resolved_home = home.resolve(strict=False) if home is not None else None
    if decision is not None:
        return _agent_sketch_decision(
            normalized_goal,
            repo=repo,
            home=resolved_home,
            decision=decision,
        )
    signals = _repo_signals(repo, normalized_goal)
    constraints = _extract_constraints(normalized_goal)
    criteria = _sketch_criteria(signals)
    candidates = _sketch_candidates(signals, criteria, normalized_goal)
    chosen = max(candidates, key=lambda item: int(item["weighted_score"]))
    rejected = [
        {
            "option": item["id"],
            "why_lost": _rejection_reason(item, chosen),
        }
        for item in candidates
        if item["id"] != chosen["id"]
    ]
    branches = _decision_branches(signals)
    flowplan_input = _flowplan_input(normalized_goal, chosen, constraints, branches)
    cited_signal = _repo_signal_summary(signals)
    no_gos = [
        "do not treat this sketch or its confidence as evidence, approval, or assurance",
        "do not launch proofrun, workers, or repository mutations from sketch",
        "do not replace an existing public path without explicit flowplan scope",
    ]
    rabbit_holes = [
        "a new orchestration lifecycle when an existing seam can carry the change",
        "speculative follow-on features not required by the observable outcome",
    ]
    riskiest_assumption = {
        "assumption": (
            "the selected existing seam can carry the behavior without hidden coupling"
            if chosen["id"] == "bounded-existing-seam"
            else "the selected isolated boundary reduces coupling without duplicating lifecycle ownership"
        ),
        "spike_or_tracer": (
            "Run a throwaway spike that answers only whether the nearest seam can expose the "
            "success signal while preserving existing boundary flags."
            if chosen["id"] == "bounded-existing-seam"
            else "Run a tracer bullet through the proposed isolated boundary to prove one end-to-end "
            "success signal without creating a parallel lifecycle."
        ),
        "already_safe": False,
    }
    decision_record = {
        "context": (
            f"Outcome: {normalized_goal}. Constraints: {'; '.join(constraints)}. "
            f"Observed repository signal: {cited_signal}"
        ),
        "decision": f"Use {chosen['id']}: {chosen['summary']}",
        "consequences": [
            chosen["selection_rationale"],
            riskiest_assumption["spike_or_tracer"],
            *[f"No-go: {item}" for item in no_gos],
        ],
    }

    return {
        "kind": "orro-sketch",
        "schema_version": ORRO_ADVISORY_DECISION_SCHEMA_VERSION,
        "authored_by": "heuristic-scaffold",
        "agent_authored": False,
        "degraded": True,
        "scaffold_scope": "code-placement-only",
        "degraded_note": (
            "This degraded scaffold evaluates code-architecture placement only; it "
            "does not preserve or answer non-code (product/ideation) goal categories. "
            "Use --decision <file> for a real agent-authored frame."
        ),
        "goal": normalized_goal,
        "repo": str(repo),
        "home": str(resolved_home) if resolved_home is not None else None,
        "method": {
            "sequence": [
                "frame",
                "criteria-first",
                "independent-divergence",
                "per-criterion-scoring",
                "kill-the-frontrunner",
                "converge",
                "de-risk",
                "resolve-without-open-menus",
                "adr-handoff",
            ],
            "rule": "derive criteria before independently generating structurally distinct options",
            "meta_principle": (
                "An AI agent's stated confidence is not evidence; only an external signal is."
            ),
        },
        "frame": {
            "outcome": normalized_goal,
            "why": f"Deliver {normalized_goal} because the observable repository behavior matters more than a proposed implementation shape.",
            "success_signal": (
                "an operator-observed focused check demonstrates the requested outcome while the "
                "existing public and advisory boundaries remain unchanged"
            ),
        },
        "criteria": criteria,
        "candidates": candidates,
        "devils_advocate": {
            "leading_option": chosen["id"],
            "case_against": (
                "The leading bounded seam may conceal coupling and make a locally small diff harder "
                "to observe or test than its score implies."
            ),
            "weakest_option": min(candidates, key=lambda item: int(item["weighted_score"]))["id"],
            "case_for": (
                "A separate subsystem becomes preferable if an isolated spike proves the current "
                "lifecycle cannot carry the success signal safely."
            ),
            "external_check": cited_signal,
        },
        "chosen": {
            "option": chosen["id"],
            "reason": chosen["selection_rationale"],
            "confidence": "moderate",
            "what_would_change_it": (
                "an isolated spike showing that the selected seam cannot expose the success signal "
                "without widening the blast radius"
            ),
            "backing_external_signal": cited_signal,
        },
        "rejected": rejected,
        "riskiest_assumption": riskiest_assumption,
        "no_gos": no_gos,
        "rabbit_holes": rabbit_holes,
        "decision_record": decision_record,
        "external_signal_check": {
            "type": "isolated-verification-question",
            "question": "What concrete repository observation supports this direction?",
            "observed_answer": cited_signal,
            "reported_verbatim": True,
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
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an agent-authored trace or emit a degraded scaffold."""

    normalized_symptom = _normalize_text(symptom)
    repo = repo.resolve(strict=False)
    resolved_home = home.resolve(strict=False) if home is not None else None
    if decision is not None:
        return _agent_trace_decision(
            normalized_symptom,
            repo=repo,
            home=resolved_home,
            decision=decision,
        )
    signals = _repo_signals(repo, normalized_symptom)
    check_the_plug = _check_the_plug(repo, signals)
    reproduction = _observed_reproduction(repo, normalized_symptom)
    red_observed = reproduction.get("red_observed") is True
    localization = _localize_trace(normalized_symptom, signals, reproduction)
    hypotheses = _trace_hypotheses(normalized_symptom, localization, reproduction) if red_observed else []
    confirmation, logbook = _falsify_hypotheses(hypotheses, reproduction)
    hypotheses = _rank_hypotheses_by_confirmation(hypotheses, confirmation)
    root_cause_or_unconfirmed = _trace_verdict(
        normalized_symptom,
        reproduction,
        localization,
        hypotheses,
        confirmation,
    )
    root_cause = root_cause_or_unconfirmed.get("root_cause")
    if isinstance(root_cause, dict) and root_cause.get("tier") == "confirmed":
        root_cause["tier"] = "suspected"
        root_cause["status"] = "unconfirmed"
        root_cause["stop_reason"] = (
            "stop at suspected: degraded heuristic scaffolds cannot author or "
            "confirm a root-cause claim"
        )
    confirmed = bool(root_cause and root_cause["tier"] == "confirmed")
    fix_scope = _trace_fix_scope(normalized_symptom, localization, reproduction)
    evidence = _trace_evidence(repo, signals, reproduction)
    ranked_hypotheses = [
        {
            "rank": rank,
            "hypothesis": item["mechanism"],
            "basis": item["prediction"],
            "evidence_for": [],
            "evidence_against": [],
            "confirmation_test": item["discriminating_probe"],
            "status": "unconfirmed",
        }
        for rank, item in enumerate(hypotheses, start=1)
    ]

    return {
        "kind": "orro-trace",
        "schema_version": ORRO_ADVISORY_DECISION_SCHEMA_VERSION,
        "authored_by": "heuristic-scaffold",
        "agent_authored": False,
        "degraded": True,
        "goal_or_symptom": normalized_symptom,
        "symptom": normalized_symptom,
        "repo": str(repo),
        "home": str(resolved_home) if resolved_home is not None else None,
        "method": {
            "sequence": [
                "frame-check-the-plug",
                "reproduce-hard-gate",
                "minimize-localize",
                "competing-hypotheses",
                "falsify-and-independently-verify",
                "root-cause-depth",
                "verdict-handoff",
            ],
            "gate": "no hypothesis or stated root cause before an observed red",
            "meta_principle": (
                "An AI agent's stated confidence is not evidence; execution is the oracle."
            ),
        },
        "check_the_plug": check_the_plug,
        "reproduction": reproduction,
        "localization": localization,
        "hypotheses": hypotheses,
        "confirmation": confirmation,
        "logbook": logbook,
        "evidence_gathered": evidence,
        "ranked_hypotheses": ranked_hypotheses,
        **root_cause_or_unconfirmed,
        "fix_scope": fix_scope,
        "investigation_phases": [
            {
                "name": "observe",
                "status": "complete",
                "result": "symptom and repository shape recorded",
            },
            {
                "name": "reproduce-localize",
                "status": "complete" if red_observed else "blocked",
                "result": (
                    f"observed red and localized with {localization['technique']}"
                    if red_observed
                    else reproduction["non_reproducible_reason"]
                ),
            },
            {
                "name": "hypothesize",
                "status": "complete" if hypotheses else "blocked",
                "result": (
                    "competing mechanisms have discriminating read-only probes"
                    if hypotheses
                    else "hard gate stopped before hypothesis generation"
                ),
            },
            {
                "name": "confirm-root-cause",
                "status": "complete" if confirmed else "blocked",
                "result": (
                    "root cause confirmed by execution and rival falsification"
                    if confirmed
                    else "root cause remains unconfirmed; fix proposal is gated"
                ),
            },
        ],
        "recommended_fix_scope": {
            "fix_proposal_allowed": confirmed,
            "allowed_paths": [fix_scope["cause_site"]] if confirmed and fix_scope["cause_site"] else [],
            "instruction": "recommend scope only; trace never edits the repository",
            "after_confirmation": (
                "limit the fix to the confirmed source and add the smallest failing reproduction "
                "test before implementation"
            ),
        },
        "flowplan_handoff": {
            "kind": "orro-flowplan-input",
            "status": "ready" if confirmed else "blocked-root-cause-unconfirmed",
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
    cited_regions = _cited_regions(repo, matching_paths, tokens)
    return {
        "repo_exists": repo.is_dir(),
        "file_count": len(files),
        "languages": [name for name, _count in language_counts.most_common(5)],
        "matching_paths": matching_paths,
        "cited_regions": cited_regions,
        "existing_advisory_patterns": advisory_patterns,
        "test_paths": test_paths,
    }


def _cited_regions(repo: Path, paths: list[str], tokens: set[str]) -> list[str]:
    citations: list[str] = []
    for relative in paths[:6]:
        path = repo / relative
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        line_number = next(
            (
                index
                for index, line in enumerate(lines, start=1)
                if any(token in line.lower() for token in tokens)
            ),
            1,
        )
        citations.append(f"{relative}:{line_number}")
    return citations


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


def _sketch_criteria(signals: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": "fit-with-architecture",
            "weight": 30,
            "repo_signal": f"existing advisory patterns: {signals['existing_advisory_patterns']}",
        },
        {
            "name": "blast-radius",
            "weight": 25,
            "repo_signal": f"observed repository size: {signals['file_count']} files",
        },
        {
            "name": "reversibility",
            "weight": 15,
            "repo_signal": "additive seams can be removed without migrating stored evidence",
        },
        {
            "name": "effort",
            "weight": 10,
            "repo_signal": f"dominant languages: {signals['languages']}",
        },
        {
            "name": "test-and-observability-cost",
            "weight": 20,
            "repo_signal": f"focused test paths: {signals['test_paths']}",
        },
    ]


def _sketch_candidates(
    signals: dict[str, Any],
    criteria: list[dict[str, Any]],
    goal: str,
) -> list[dict[str, Any]]:
    has_existing_seam = bool(signals["existing_advisory_patterns"])
    goal_lower = goal.lower()
    isolation_requested = any(
        marker in goal_lower
        for marker in ("isolate", "isolated", "new boundary", "separate module")
    )
    parallel_system_requested = any(
        marker in goal_lower
        for marker in ("new subsystem", "parallel subsystem", "separate lifecycle")
    )
    crowded_repo = int(signals["file_count"]) >= 10
    bounded_fit = 1 if parallel_system_requested else (5 if has_existing_seam else 2)
    bounded_blast_radius = 2 if parallel_system_requested else 5
    first_summary = (
        "extend the nearest existing advisory seam with a bounded additive path"
        if has_existing_seam
        else "add the smallest isolated feature slice beside the nearest existing pattern"
    )
    candidates = [
        {
            "id": "bounded-existing-seam",
            "axis": "where logic lives: extend the nearest existing seam",
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
            "per_criterion_scores": {
                "fit-with-architecture": bounded_fit,
                "blast-radius": bounded_blast_radius,
                "reversibility": 5,
                "effort": 5,
                "test-and-observability-cost": 4,
            },
            "forcing_prompt": "first principles: reuse only what the observed repo already proves",
        },
        {
            "id": "isolated-module-adapter",
            "axis": "boundary shape: isolate logic behind the existing entrypoint",
            "summary": "introduce a focused module behind the existing public entrypoint",
            "shape": "separate decision construction from CLI and persistence while retaining current aliases",
            "benefits": ["clear unit boundary", "direct tests", "future internal evolution"],
            "risks": ["one additional module and integration seam"],
            "tradeoffs": ["cleaner isolation in exchange for slightly more structure"],
            "selection_rationale": "use when the existing entrypoint is already crowded or responsibilities differ",
            "per_criterion_scores": {
                "fit-with-architecture": 5 if isolation_requested and not has_existing_seam else 4,
                "blast-radius": 4,
                "reversibility": 4,
                "effort": 4 if isolation_requested or crowded_repo else 3,
                "test-and-observability-cost": 5,
            },
            "forcing_prompt": "SCAMPER: separate the decision core without replacing the public path",
        },
        {
            "id": "new-parallel-subsystem",
            "axis": "system ownership: create a new subsystem and lifecycle",
            "summary": "create a new subsystem with its own orchestration and artifact lifecycle",
            "shape": "separate parser, state, and workflow ownership",
            "benefits": ["maximum independence"],
            "risks": ["duplicate lifecycle", "larger public surface", "higher maintenance cost"],
            "tradeoffs": ["more autonomy in exchange for duplication and migration risk"],
            "selection_rationale": "reserve for evidence that existing seams cannot carry the behavior safely",
            "per_criterion_scores": {
                "fit-with-architecture": 5 if parallel_system_requested else 1,
                "blast-radius": 4 if parallel_system_requested else 1,
                "reversibility": 4 if parallel_system_requested else 2,
                "effort": 3 if parallel_system_requested else 1,
                "test-and-observability-cost": 4 if parallel_system_requested else 2,
            },
            "forcing_prompt": "inversion: assume existing ownership is the constraint and isolate everything",
        },
    ]
    for candidate in candidates:
        scores = candidate["per_criterion_scores"]
        candidate["weighted_score"] = sum(
            int(item["weight"]) * int(scores[str(item["name"])])
            for item in criteria
        )
        candidate["score_basis"] = {
            "existing_seam": has_existing_seam,
            "repo_file_count": signals["file_count"],
            "goal_requests_isolation": isolation_requested,
            "goal_requests_parallel_system": parallel_system_requested,
        }
    return candidates


def _rejection_reason(candidate: dict[str, Any], chosen: dict[str, Any]) -> str:
    return (
        f"lost to {chosen['id']} on the predeclared weighted criteria "
        f"({candidate['weighted_score']} vs {chosen['weighted_score']}) and carries "
        f"the tradeoff: {candidate['tradeoffs'][0]}"
    )


def _repo_signal_summary(signals: dict[str, Any]) -> str:
    return (
        f"file_count={signals['file_count']}; languages={signals['languages']}; "
        f"matching_paths={signals['matching_paths']}; "
        f"existing_advisory_patterns={signals['existing_advisory_patterns']}; "
        f"test_paths={signals['test_paths']}"
    )


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


def _check_the_plug(repo: Path, signals: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_exists": repo.is_dir(),
        "python_version": sys.version.split()[0],
        "git_branch": _git_branch(repo),
        "witnessd_depone_root": os.environ.get("WITNESSD_DEPONE_ROOT"),
        "file_count": signals["file_count"],
        "cheap_non_bugs_checked": [
            "repository path exists",
            "effective Python version captured",
            "current git branch captured when available",
            "test discovery presence checked",
        ],
        "worked_before": "unknown; no external observation supplied",
    }


def _git_branch(repo: Path) -> str | None:
    git_path = repo / ".git"
    try:
        if git_path.is_file():
            marker = git_path.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir: "):
                return None
            git_path = (repo / marker.removeprefix("gitdir: ")).resolve(strict=False)
        head = (git_path / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: refs/heads/"):
        return None
    return head.removeprefix("ref: refs/heads/")


def _observed_reproduction(repo: Path, symptom: str) -> dict[str, Any]:
    receipt_path = repo / "orro-trace-reproduction.json"
    if not receipt_path.is_file():
        return _missing_reproduction("no orro-trace-reproduction.json found")
    try:
        if receipt_path.stat().st_size > 262_144:
            return _missing_reproduction("reproduction receipt exceeds the 256 KiB read limit")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _missing_reproduction(f"malformed reproduction receipt: {exc}")
    if not isinstance(receipt, dict) or receipt.get("kind") != "orro-trace-reproduction":
        return _missing_reproduction("malformed reproduction receipt: invalid kind")

    receipt_symptom = receipt.get("symptom")
    command = receipt.get("command")
    exit_code = receipt.get("exit_code")
    stdout = receipt.get("stdout")
    stderr = receipt.get("stderr")
    minimized = receipt.get("minimized")
    external_confirmation = receipt.get("external_confirmation")
    valid_shape = all(
        (
            isinstance(receipt_symptom, str),
            isinstance(command, list) and all(isinstance(item, str) for item in command),
            isinstance(exit_code, int) and not isinstance(exit_code, bool),
            isinstance(stdout, str),
            isinstance(stderr, str),
            isinstance(minimized, bool),
        )
    )
    if not valid_shape:
        return _missing_reproduction("malformed reproduction receipt: invalid field shape")
    if external_confirmation is not None and not _valid_external_confirmation(
        external_confirmation
    ):
        return _missing_reproduction(
            "malformed reproduction receipt: invalid external confirmation"
        )

    output = _combined_output(stdout, stderr)
    suite_red_observed = exit_code != 0
    symptom_bound = _normalize_text(receipt_symptom) == symptom
    red_observed = suite_red_observed and symptom_bound
    reproduction: dict[str, Any] = {
        "status": "observed-red" if red_observed else "not-reproduced",
        "steps": [
            "read the prior actual-run receipt without executing its command",
            "bind the recorded symptom exactly to the requested symptom",
            f"capture recorded exit={exit_code} and stdout/stderr verbatim",
        ],
        "minimized": minimized,
        "red_observed": red_observed,
        "suite_red_observed": suite_red_observed,
        "symptom_bound": symptom_bound,
        "observed_output": output,
        "exit_code": exit_code,
        "command": command,
        "source": receipt_path.name,
        "command_executed_by_trace": False,
        "external_confirmation": external_confirmation,
    }
    if suite_red_observed and not red_observed:
        reproduction["non_reproducible_reason"] = (
            "cannot localize; the prior-run red is not bound to the requested symptom"
        )
    elif not red_observed:
        reproduction["non_reproducible_reason"] = (
            "cannot localize; the prior-run receipt did not record an observed red"
        )
    return reproduction


def _valid_external_confirmation(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(
        (
            isinstance(value.get("discriminating_probe_ran"), bool),
            isinstance(value.get("ruled_out_rival"), bool),
            isinstance(value.get("red_to_green_observed"), bool),
            isinstance(value.get("reported_verbatim"), str),
            bool(value.get("reported_verbatim", "").strip()),
        )
    )


def _missing_reproduction(observed_output: str) -> dict[str, Any]:
    return {
        "status": "not-reproduced",
        "steps": [],
        "minimized": False,
        "non_reproducible_reason": (
            "cannot localize; need a symptom-bound actual-run reproduction receipt or concrete failure log"
        ),
        "red_observed": False,
        "suite_red_observed": False,
        "observed_output": observed_output,
        "source": None,
        "command": [],
        "command_executed_by_trace": False,
        "external_confirmation": None,
    }


def _combined_output(stdout: str, stderr: str) -> str:
    combined = "\n".join(part.rstrip() for part in (stdout, stderr) if part).strip()
    return combined[-12000:]


def _localize_trace(
    symptom: str,
    signals: dict[str, Any],
    reproduction: dict[str, Any],
) -> dict[str, Any]:
    lower = symptom.lower()
    if "regression" in lower or "worked before" in lower:
        technique = "git bisect over known-good and observed-red revisions"
    elif "config" in lower or "configuration" in lower or "diff" in lower:
        technique = "delta-minimize configuration or changed inputs"
    else:
        technique = "delta-minimize the reproducer, then trace to the first divergent code region"
    cited = signals["cited_regions"]
    if reproduction.get("red_observed") is not True:
        cited = []
    return {
        "technique": technique,
        "suspect_region_cited": cited,
        "candidate_paths": signals["matching_paths"] if cited else [],
        "localize_before_hypothesize": True,
    }


def _trace_hypotheses(
    symptom: str,
    localization: dict[str, Any],
    reproduction: dict[str, Any],
) -> list[dict[str, Any]]:
    cited = localization["suspect_region_cited"]
    cited_text = ", ".join(cited) if cited else "the first divergent test boundary"
    return [
        {
            "id": "H1",
            "mechanism": f"localized implementation logic at {cited_text} violates the expected invariant",
            "prediction": "the observed red names the localized component or its failing behavior",
            "discriminating_probe": (
                "scan the verbatim failing output for localized path or symbol tokens; this differs "
                "from an environment-only failure"
            ),
            "confidence": "prior-low",
            "distinct_mechanism": "implementation logic",
        },
        {
            "id": "H2",
            "mechanism": "effective configuration or environment changes the runtime behavior",
            "prediction": "the observed red reports configuration, environment, import, or dependency state",
            "discriminating_probe": (
                "scan the verbatim failing output for config, environment, import, or dependency markers; "
                "this differs from a value assertion in localized logic"
            ),
            "confidence": "prior-low",
            "distinct_mechanism": "runtime configuration",
        },
        {
            "id": "H3",
            "mechanism": "the reproduction expectation is stale rather than the implementation being wrong",
            "prediction": f"the failure output for '{symptom}' lacks a stable expected-versus-actual assertion",
            "discriminating_probe": (
                "inspect the verbatim red for an explicit assertion mismatch; a concrete mismatch weakens "
                "the stale-expectation mechanism"
            ),
            "confidence": "prior-low",
            "distinct_mechanism": "test oracle",
        },
    ]


def _falsify_hypotheses(
    hypotheses: list[dict[str, Any]],
    reproduction: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not hypotheses:
        return (
            {
                "lint_ran": False,
                "lint_only": True,
                "can_confirm": False,
                "ruled_out_rival": False,
                "verification_questions": [],
            },
            [],
        )
    output = str(reproduction.get("observed_output", ""))
    lower = output.lower()
    assertion_tokens = ("assertionerror", " != ", "expected", "actual")
    environment_tokens = (
        "config",
        "environment",
        "importerror",
        "modulenotfounderror",
        "no module named",
        "dependency",
    )
    results = {
        "H1": any(token in lower for token in assertion_tokens),
        "H2": any(token in lower for token in environment_tokens),
        "H3": not any(token in lower for token in assertion_tokens),
    }
    logbook = []
    for hypothesis in hypotheses:
        supported = results[str(hypothesis["id"])]
        logbook.append(
            {
                "hypothesis": hypothesis["id"],
                "probe": hypothesis["discriminating_probe"],
                "result": f"probe_result={supported}; observed_output={output}",
                "outcome": "survives" if supported else "falsified",
                "reflexion": (
                    None
                    if supported
                    else "Do not revisit this mechanism unless new external evidence contradicts the probe."
                ),
            }
        )
    logbook.sort(key=lambda item: item["outcome"] != "survives")
    ruled_out = any(not result for result in results.values())
    confirmation = {
        "lint_ran": True,
        "lint_only": True,
        "can_confirm": False,
        "ruled_out_rival": ruled_out,
        "verification_questions": [
            {
                "question": "Did a prior actual run produce a symptom-bound red?",
                "answer": f"{reproduction.get('red_observed')}; exit={reproduction.get('exit_code')}",
            },
            {
                "question": "Does the verbatim output contain an assertion-style mismatch?",
                "answer": str(results["H1"]),
            },
            {
                "question": "Does the verbatim output identify environment/configuration failure markers?",
                "answer": str(results["H2"]),
            },
        ],
        "supported_hypotheses": [
            hypothesis_id for hypothesis_id, supported in results.items() if supported
        ],
        "ruled_out_hypotheses": [
            hypothesis_id for hypothesis_id, supported in results.items() if not supported
        ],
    }
    return confirmation, logbook


def _rank_hypotheses_by_confirmation(
    hypotheses: list[dict[str, Any]],
    confirmation: dict[str, Any],
) -> list[dict[str, Any]]:
    supported = [str(item) for item in confirmation.get("supported_hypotheses", [])]
    rank = {hypothesis_id: index for index, hypothesis_id in enumerate(supported)}
    return sorted(
        hypotheses,
        key=lambda item: (str(item["id"]) not in rank, rank.get(str(item["id"]), len(rank))),
    )


def _trace_verdict(
    symptom: str,
    reproduction: dict[str, Any],
    localization: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    confirmation: dict[str, Any],
) -> dict[str, Any]:
    if reproduction.get("red_observed") is not True:
        return {
            "unconfirmed": {
                "best_hypothesis": None,
                "missing_evidence": (
                    "need an observed red from a symptom-bound prior actual-run receipt or concrete failure trace before localization"
                ),
            }
        }
    supported = set(str(item) for item in confirmation.get("supported_hypotheses", []))
    surviving = next(
        (item for item in hypotheses if str(item["id"]) in supported),
        None,
    )
    if surviving is None:
        return {
            "unconfirmed": {
                "best_hypothesis": hypotheses[0]["mechanism"] if hypotheses else None,
                "missing_evidence": "all discriminating predictions failed; need a new external probe",
            }
        }
    cause_site = (
        localization["suspect_region_cited"][0]
        if localization["suspect_region_cited"]
        else None
    )
    external_confirmation = reproduction.get("external_confirmation")
    confirmed = bool(
        isinstance(external_confirmation, dict)
        and external_confirmation.get("discriminating_probe_ran") is True
        and external_confirmation.get("ruled_out_rival") is True
        and external_confirmation.get("red_to_green_observed") is True
    )
    tier = "confirmed" if confirmed else "suspected"
    return {
        "root_cause": {
            "status": "confirmed" if confirmed else "unconfirmed",
            "tier": tier,
            "finding": surviving["mechanism"],
            "backing_artifact": reproduction["observed_output"],
            "external_confirmation": external_confirmation,
            "depth_chain": [
                f"symptom: {symptom}",
                "why: the prior actual run records an assertion failure",
                f"why: the first repo-cited suspect region is {cause_site or 'not yet isolated'}",
            ],
            "stop_reason": (
                "stop at confirmed: the prior external receipt records discrimination, rival rejection, and red-to-green"
                if confirmed
                else "stop at suspected: no external intervention demonstrated red-to-green, and trace is read-only"
            ),
        }
    }


def _trace_fix_scope(
    symptom: str,
    localization: dict[str, Any],
    reproduction: dict[str, Any],
) -> dict[str, Any]:
    cited = localization["suspect_region_cited"]
    return {
        "cause_site": cited[0] if cited else None,
        "blast_radius": localization["candidate_paths"],
        "invariant": f"the behavior described by '{symptom}' must match the observed expected result",
        "regression_test": (
            f"rerun {' '.join(reproduction.get('command', []))} and require the observed red to turn green"
            if reproduction.get("command")
            else "add the smallest deterministic reproduction before any fix"
        ),
        "implemented": False,
    }


def _trace_evidence(
    repo: Path,
    signals: dict[str, Any],
    reproduction: dict[str, Any],
) -> list[dict[str, Any]]:
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
            "observation": reproduction["observed_output"],
            "interpretation": (
                "a prior actual execution is an external signal"
                if reproduction.get("red_observed")
                else "root cause and fix scope must remain unconfirmed"
            ),
        },
    ]


def _advisory_boundary(*, executes_commands: bool = False) -> dict[str, bool]:
    return {
        "advisory_only": True,
        "is_evidence": False,
        "raises_assurance": False,
        "verifies_evidence": False,
        "can_change_evidence_verdict": False,
        "executes_proofrun": False,
        "executes_commands": executes_commands,
        "runs_workers": False,
        "calls_depone": False,
        "mutates_repo": False,
        "approves_merge": False,
    }


def _status_note() -> str:
    return (
        "Advisory only: not proof, verifier truth, approval, or assurance; "
        "cannot change an evidence verdict. A sealed record provides auditable "
        "provenance, not correctness of the chosen direction or root cause."
    )
