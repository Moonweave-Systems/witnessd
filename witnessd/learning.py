"""W5 versioned learning deltas with provenance-gated promotion."""

from __future__ import annotations

import json
import os
from typing import Any

from witnessd.canonical import canonical_hash
from witnessd.runintent import build_run_intent, write_signed_run_intent
from witnessd.runlog import append_runlog
from witnessd.substrate import build_bundle

LEARNING_DELTA_KIND = "witnessd-learning-delta"
LEARNING_SCHEMA_VERSION = "1.0"
APPROVAL_EVENT = "learning_approval"
ERR_LEARNING_PROVENANCE_MISSING = "ERR_LEARNING_PROVENANCE_MISSING"
ERR_LEARNING_PROVENANCE_MISMATCH = "ERR_LEARNING_PROVENANCE_MISMATCH"
ERR_LEARNING_DELTA_UNAPPROVED = "ERR_LEARNING_DELTA_UNAPPROVED"


def build_learning_delta(
    *,
    run_id: str,
    target: str,
    version: int,
    delta_text: str,
    capture_manifest: dict[str, Any],
    approval_event_hash: str,
    provenance_manifest_hash: str,
) -> dict[str, Any]:
    return {
        "kind": LEARNING_DELTA_KIND,
        "schema_version": LEARNING_SCHEMA_VERSION,
        "run_id": run_id,
        "target": target,
        "version": version,
        "delta_text": delta_text,
        "provenance": {
            "capture_hash": canonical_hash(capture_manifest),
            "approval_event_hash": approval_event_hash,
            "provenance_manifest_hash": provenance_manifest_hash,
        },
    }


def validate_learning_delta_provenance(
    delta: dict[str, Any],
    *,
    committed_captures: list[dict[str, Any]],
    approval_events: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    provenance = delta.get("provenance") if isinstance(delta.get("provenance"), dict) else {}
    capture_hash = provenance.get("capture_hash")
    approval_hash = provenance.get("approval_event_hash")
    provenance_manifest_hash = provenance.get("provenance_manifest_hash")
    if not capture_hash or not approval_hash or not provenance_manifest_hash:
        return [ERR_LEARNING_PROVENANCE_MISSING]

    committed_hashes = {canonical_hash(capture) for capture in committed_captures}
    if capture_hash not in committed_hashes:
        errors.append(ERR_LEARNING_PROVENANCE_MISMATCH)

    approved_hashes = {
        event.get("event_hash")
        for event in approval_events
        if event.get("event") == APPROVAL_EVENT
    }
    if approval_hash not in approved_hashes:
        errors.append(ERR_LEARNING_DELTA_UNAPPROVED)
    return errors


def promote_learning_delta(
    delta: dict[str, Any],
    *,
    log,
    run_id: str,
    priv: str,
    pub: str,
    committed_captures: list[dict[str, Any]],
    approval_events: list[dict[str, Any]],
    evidence_dir: str,
) -> dict[str, Any]:
    from witnessd.pause import PauseError, assert_not_paused

    try:
        assert_not_paused(log.read())
    except PauseError as exc:
        append_runlog(
            log,
            run_id,
            "learning_delta",
            error_code=exc.code,
            payload={"blocked": True, "errors": [exc.code], "target": delta.get("target")},
        )
        return {"promoted": False, "errors": [exc.code]}

    errors = validate_learning_delta_provenance(
        delta,
        committed_captures=committed_captures,
        approval_events=approval_events,
    )
    if errors:
        append_runlog(
            log,
            run_id,
            "learning_delta",
            error_code=errors[0],
            payload={"blocked": True, "errors": errors, "target": delta.get("target")},
        )
        return {"promoted": False, "errors": errors}

    os.makedirs(evidence_dir, exist_ok=True)
    delta_path = os.path.join(evidence_dir, "learning-delta.json")
    with open(delta_path, "w", encoding="utf-8") as handle:
        json.dump(delta, handle, sort_keys=True, indent=2)
        handle.write("\n")
    run_intent_path = os.path.join(evidence_dir, "run-intent.json")
    run_intent = build_run_intent(
        run_id=run_id,
        baseline={"capture_hash": delta["provenance"]["capture_hash"]},
        allowed_paths=[str(delta.get("target", ""))],
        approval_policy="operator-approved",
        sandbox_mode="learning-promotion",
        provider="witnessd-learning",
        instruction_hashes={"delta_sha256": canonical_hash(delta)},
        budgets={},
        capture_profile="full",
    )
    write_signed_run_intent(
        run_intent_path,
        run_intent,
        priv,
        key_id="witnessd-learning-operator",
    )

    manifest = {
        "kind": LEARNING_DELTA_KIND,
        "assurance": "A1-local-observed",
        "decision": "learning-delta-promoted",
        "prev_capture_hash": delta["provenance"]["capture_hash"],
    }
    bundle = build_bundle(
        manifest,
        {"learning-delta": delta_path, "run-intent": run_intent_path},
        priv,
        pub,
        key_id="witnessd-learning-operator",
        otel_spans=[],
    )
    append_runlog(
        log,
        run_id,
        "learning_delta",
        payload={
            "promoted": True,
            "target": delta.get("target"),
            "version": delta.get("version"),
            "capture_hash": delta["provenance"]["capture_hash"],
        },
    )
    return {
        "promoted": True,
        "bundle": bundle,
        "artifact_paths": {"learning-delta": delta_path, "run-intent": run_intent_path},
        "delta_path": delta_path,
    }


def _self_test() -> None:
    cap = {"kind": "agent-fabric-capture-manifest", "assurance": "A1-local-observed"}
    approval = {"event": APPROVAL_EVENT, "event_hash": "abc"}
    delta = build_learning_delta(
        run_id="R",
        target="AGENTS.md",
        version=1,
        delta_text="x",
        capture_manifest=cap,
        approval_event_hash="abc",
        provenance_manifest_hash=canonical_hash(cap),
    )
    assert validate_learning_delta_provenance(
        delta, committed_captures=[cap], approval_events=[approval]
    ) == []
