"""Runner-receipt builder (E5, runner_kind=manual).

witnessd's shell lane is not driven by an agent runner, so it records the run as
a `manual` runner receipt: the observer-declared invocation, its exit code, and
the files it touched. Depone's `validate_runner_receipt` re-derives the shape
from these bytes. Per §4.6 the receipt self-hashes over every field *except*
`source_hashes` itself, so `source_hashes.receipt` binds the receipt content
without a circular dependency.
"""

from __future__ import annotations

from typing import Any

from witnessd.canonical import canonical_hash

RUNNER_RECEIPT_KIND = "agent-fabric-runner-receipt"
RUNNER_RECEIPT_VERSION = "1.0"
RUNNER_KIND_MANUAL = "manual"
ARM_GOVERNED = "governed"


def build_runner_receipt(
    *,
    task_id: str,
    worktree: str,
    invocation: list[str],
    transcript_path: str,
    exit_code: int,
    touched_files: list[str],
    started_at: str,
    ended_at: str,
    arm: str = ARM_GOVERNED,
    runner_kind: str = RUNNER_KIND_MANUAL,
    human_intervened: bool = False,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "kind": RUNNER_RECEIPT_KIND,
        "schema_version": RUNNER_RECEIPT_VERSION,
        "runner_kind": runner_kind,
        "arm": arm,
        "task_id": task_id,
        "worktree": worktree,
        "invocation": list(invocation),
        "transcript_path": transcript_path,
        "exit_code": exit_code,
        "touched_files": list(touched_files),
        "started_at": started_at,
        "ended_at": ended_at,
        "human_intervened": human_intervened,
    }
    receipt["source_hashes"] = {"receipt": canonical_hash(receipt)}
    return receipt
