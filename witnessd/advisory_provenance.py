"""Seal ORRO advisory decisions for Depone v108 provenance re-derivation.

This module records tamper-evident advisory provenance. It does not establish
that a sketch direction is correct or that a trace root cause is true, and it
does not change any execution-evidence verdict or assurance level.
"""

from __future__ import annotations

import base64
import copy
import json
import shlex
from pathlib import Path
from typing import Any

from witnessd.canonical import canonical_hash
from witnessd.signing import derive_public_key_id, gen_operator_keypair, sign_dsse


ADVISORY_PROVENANCE_SCHEMA_VERSION = "v108.advisory_provenance"
ADVISORY_PROVENANCE_PREDICATE_TYPE = (
    "https://depone.dev/attestations/advisory-provenance/v108"
)
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
ADVISORY_PROVENANCE_BUNDLE = "advisory-provenance-bundle.json"
EVIDENCE_CONTRACT = "evidence-contract.json"
TRACE_RECEIPT = "orro-trace-reproduction.json"


def emit_advisory_provenance(
    decision: dict[str, Any],
    *,
    decision_path: Path,
    home: Path,
    repo: Path,
) -> dict[str, Any]:
    """Write a v108 decision, optional trace receipt, DSSE bundle, and contract."""

    sealed_decision = copy.deepcopy(decision)
    subjects: list[dict[str, Any]] = []
    receipt = _sealed_trace_receipt(sealed_decision, repo=repo)
    if receipt is not None:
        sealed_decision.setdefault("reproduction", {})["receipt_sha256"] = (
            canonical_hash(receipt)
        )
        _bind_trace_confirmation(sealed_decision, receipt)

    _bind_sketch_direction(sealed_decision)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(decision_path, sealed_decision)
    subjects.append(_subject(decision_path.name, sealed_decision))

    if receipt is not None:
        receipt_path = decision_path.parent / TRACE_RECEIPT
        _write_json(receipt_path, receipt)
        subjects.append(_subject(TRACE_RECEIPT, receipt))

    statement = {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": ADVISORY_PROVENANCE_PREDICATE_TYPE,
        "predicate": {"schema_version": ADVISORY_PROVENANCE_SCHEMA_VERSION},
    }
    payload = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    keys_dir = home.resolve(strict=False) / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    private_key, public_key = gen_operator_keypair(str(keys_dir))
    envelope = sign_dsse(
        {
            "payloadType": DSSE_PAYLOAD_TYPE,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": [],
        },
        private_key,
        key_id=derive_public_key_id(public_key),
    )
    _write_json(decision_path.parent / ADVISORY_PROVENANCE_BUNDLE, envelope)
    contract = {
        "schema_version": ADVISORY_PROVENANCE_SCHEMA_VERSION,
        "advisory_provenance": {
            "decision_path": decision_path.name,
            "bundle_path": ADVISORY_PROVENANCE_BUNDLE,
        },
    }
    _write_json(decision_path.parent / EVIDENCE_CONTRACT, contract)
    return sealed_decision


def _bind_sketch_direction(decision: dict[str, Any]) -> None:
    if decision.get("kind") != "orro-sketch":
        return
    chosen = decision.get("chosen")
    candidates = decision.get("candidates")
    if not isinstance(chosen, dict) or not isinstance(candidates, list):
        return
    chosen_id = chosen.get("option")
    if chosen_id is None:
        # Agent-authored decisions state chosen.direction directly and carry no
        # heuristic-only "option" id; never rebind their direction (a None id
        # would otherwise match the first candidate and overwrite the choice).
        return
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and item.get("id") == chosen_id
        ),
        None,
    )
    if isinstance(candidate, dict) and isinstance(candidate.get("axis"), str):
        chosen["direction"] = candidate["axis"]


def _sealed_trace_receipt(
    decision: dict[str, Any], *, repo: Path
) -> dict[str, Any] | None:
    if decision.get("kind") != "orro-trace":
        return None
    return load_trace_reproduction_subject(repo)


def load_trace_reproduction_subject(repo: Path) -> dict[str, Any] | None:
    """Normalize the external trace receipt exactly as it will be sealed."""

    source_path = repo.resolve(strict=False) / TRACE_RECEIPT
    if not source_path.is_file():
        return None
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(source, dict) or source.get("kind") != "orro-trace-reproduction":
        return None
    command = source.get("command")
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        command_value: str | list[str] = shlex.join(command)
    elif isinstance(command, str):
        command_value = command
    else:
        return None
    exit_status = source.get("exit_status", source.get("exit_code"))
    if not isinstance(exit_status, int) or isinstance(exit_status, bool):
        return None
    output = source.get("output")
    if not isinstance(output, str):
        stdout = source.get("stdout")
        stderr = source.get("stderr")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            return None
        output = "\n".join(part.rstrip() for part in (stdout, stderr) if part).strip()
    return {
        "kind": "orro-trace-reproduction",
        "command": command_value,
        "exit_status": exit_status,
        "output": output,
    }


def _bind_trace_confirmation(decision: dict[str, Any], receipt: dict[str, Any]) -> None:
    reproduction = decision.get("reproduction")
    if isinstance(reproduction, dict) and not isinstance(
        reproduction.get("symptom"), str
    ):
        symptom = decision.get("symptom") or decision.get("goal_or_symptom")
        if isinstance(symptom, str):
            reproduction["symptom"] = symptom
    root_cause = decision.get("root_cause")
    hypotheses = decision.get("hypotheses")
    confirmation = decision.get("confirmation")
    if (
        not isinstance(root_cause, dict)
        or not isinstance(hypotheses, list)
        or not isinstance(confirmation, dict)
    ):
        return
    hypothesis_index = root_cause.get("hypothesis_index")
    if not (
        isinstance(hypothesis_index, int)
        and not isinstance(hypothesis_index, bool)
        and 0 <= hypothesis_index < len(hypotheses)
        and isinstance(hypotheses[hypothesis_index], dict)
    ):
        finding = root_cause.get("finding", root_cause.get("summary"))
        hypothesis_index = next(
            (
                index
                for index, hypothesis in enumerate(hypotheses)
                if isinstance(hypothesis, dict)
                and hypothesis.get("mechanism") == finding
            ),
            None,
        )
    if hypothesis_index is None:
        return
    root_cause["hypothesis_index"] = hypothesis_index
    hypothesis = hypotheses[hypothesis_index]
    mechanism = hypothesis.get("mechanism")
    if isinstance(mechanism, str):
        root_cause.setdefault("finding", mechanism)
        root_cause.setdefault("summary", mechanism)
    ruled_out_ids = {str(item) for item in confirmation.get("ruled_out_hypotheses", [])}
    existing_ruled_out = confirmation.get("rival_hypotheses_ruled_out")
    if not isinstance(existing_ruled_out, list):
        confirmation["rival_hypotheses_ruled_out"] = [
            index
            for index, candidate in enumerate(hypotheses)
            if index != hypothesis_index
            and isinstance(candidate, dict)
            and str(candidate.get("id")) in ruled_out_ids
        ]
    if root_cause.get("tier") != "confirmed":
        return
    output = receipt.get("output")
    if decision.get("agent_authored") is True:
        planned_probe = hypothesis.get("discriminating_probe")
        observed_probe = (
            planned_probe
            if isinstance(planned_probe, str)
            and isinstance(output, str)
            and planned_probe in output
            else None
        )
    else:
        observed_probe = _observed_trace_probe(hypothesis, output)
    reproduction_is_backed = (
        isinstance(reproduction, dict)
        and reproduction.get("red_observed") is True
        and isinstance(reproduction.get("symptom"), str)
        and isinstance(output, str)
        and reproduction["symptom"] in output
    )
    if (
        observed_probe is None
        or not reproduction_is_backed
        or not confirmation["rival_hypotheses_ruled_out"]
    ):
        root_cause["tier"] = "suspected"
        root_cause["status"] = "unconfirmed"
        root_cause["stop_reason"] = (
            "stop at suspected: sealed bytes do not contain every observation "
            "required to re-derive a confirmed advisory provenance claim"
        )
        return
    if decision.get("agent_authored") is not True:
        planned_probe = hypothesis.get("discriminating_probe")
        if isinstance(planned_probe, str):
            hypothesis["planned_discriminating_probe"] = planned_probe
        hypothesis["discriminating_probe"] = observed_probe


def _observed_trace_probe(hypothesis: dict[str, Any], output: Any) -> str | None:
    if not isinstance(output, str):
        return None
    mechanism = hypothesis.get("distinct_mechanism")
    if mechanism == "implementation logic":
        tokens = ("assertionerror", " != ", "expected", "actual")
    elif mechanism == "runtime configuration":
        tokens = (
            "config",
            "environment",
            "importerror",
            "modulenotfounderror",
            "no module named",
            "dependency",
        )
    else:
        return None
    for line in output.splitlines():
        observed = line.strip()
        if observed and any(token in observed.lower() for token in tokens):
            return observed
    return None


def _subject(name: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "digest": {"sha256": canonical_hash(value)}}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
