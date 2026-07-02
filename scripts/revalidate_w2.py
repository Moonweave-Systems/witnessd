"""Re-derive W2 claims from committed fixture bytes.

This is the W2 G2 gate: witnessd may emit bytes, but the claims are accepted
only when independent projections/validators re-derive them from fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain
from depone.agent_fabric.isolation import verify_isolation_boundary
from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.liveness import HEARTBEAT_TTL_SECONDS, derive_liveness
from witnessd.runlog import verify_runlog

FIX = REPO_ROOT / "fixtures" / "w2"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _revalidate_liveness() -> None:
    records = _load_jsonl(FIX / "liveness-killed.jsonl")
    _require(
        verify_runlog(records) == {"ok": True, "broken_at": None},
        "liveness-killed runlog chain must verify",
    )
    last_mono = max(record["ts_monotonic"] for record in records)
    states = derive_liveness(
        records,
        now_monotonic=last_mono + HEARTBEAT_TTL_SECONDS + 1,
    )
    _require(
        "active" not in states.values(),
        f"killed/zombie fixture must not project active, got {states!r}",
    )


def _revalidate_a2() -> None:
    manifest = _load_json(FIX / "capture-manifest-a2.json")
    _require(
        validate_capture_manifest(manifest) == [],
        "W2 A2 manifest must pass Depone capture validation",
    )
    _require(
        verify_capture_chain([manifest])["decision"] == "pass",
        "W2 A2 manifest must remain valid as a W1 capture-chain genesis",
    )
    _require(
        manifest["assurance"] == "A2-isolated-observed",
        f"W2 A2 manifest assurance mismatch: {manifest['assurance']!r}",
    )
    _require(
        verify_isolation_boundary(manifest["isolation"])["boundary"] is True,
        "W2 A2 isolation facts must independently establish a boundary",
    )


def _revalidate_negative_isolation() -> None:
    same_uid = _load_json(FIX / "negative" / "capture-manifest-a2-sameuid.json")
    _require(
        validate_capture_manifest(same_uid) == [],
        "same-uid downgrade manifest must still be structurally valid",
    )
    _require(
        same_uid["assurance"] == "A1-local-observed",
        f"same-uid fixture must downgrade to A1, got {same_uid['assurance']!r}",
    )

    forged = _load_json(FIX / "negative" / "capture-manifest-a2-forged.json")
    errors = validate_capture_manifest(forged)
    _require(
        any("does not establish a privilege boundary" in error for error in errors),
        f"forged A2 facts must be blocked by Depone, got {errors!r}",
    )


def _revalidate_runner_receipt() -> None:
    receipt = _load_json(FIX / "runner-receipt.json")
    _require(
        validate_runner_receipt(receipt) == [],
        "W2 runner receipt must pass Depone validation",
    )


def _revalidate_durable_resume() -> None:
    before = _load_jsonl(FIX / "durable-resume" / "runlog-before.jsonl")
    after = _load_jsonl(FIX / "durable-resume" / "runlog-after.jsonl")
    session = _load_json(FIX / "durable-resume" / "session.json")
    _require(
        verify_runlog(before) == {"ok": True, "broken_at": None},
        "durable resume pre-crash runlog must verify",
    )
    _require(
        verify_runlog(after) == {"ok": True, "broken_at": None},
        "durable resume post-resume runlog must verify",
    )
    _require(
        after[: len(before)] == before,
        "post-resume runlog must preserve the pre-crash prefix exactly",
    )
    run_ids = {record["run_id"] for record in after}
    _require(
        run_ids == {session["run_id"]},
        f"resume runlog/session must use one run_id, got {run_ids!r}",
    )
    _require(
        after[len(before)]["prev_event_hash"] == before[-1]["event_hash"],
        "resume event must continue from the pre-crash event_hash",
    )
    _require(
        session["tool_call_cursor"] == 1,
        "durable resume must preserve the tool_call_cursor",
    )
    _require(
        session["run_state"] == "evidence-pending",
        f"resume state must be evidence-pending, got {session['run_state']!r}",
    )
    _require(
        session["idempotency_reapplied"] == 0,
        "resume fixture must not reapply completed tool calls",
    )
    _require(
        session["last_event_hash"] == after[-1]["event_hash"],
        "session must point at the resumed runlog tail",
    )


def main() -> int:
    _revalidate_liveness()
    _revalidate_a2()
    _revalidate_negative_isolation()
    _revalidate_runner_receipt()
    _revalidate_durable_resume()
    print("W2 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
