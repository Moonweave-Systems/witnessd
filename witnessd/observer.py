"""Observer/runner separation, fail-closed (E1, §4.1 B6).

Mirrors Depone's `observe.enforce_observer_separation` semantics: observer
outputs (--out / --log) must live OUTSIDE the runner sandbox. If the check
fails, no file is written (no partial output) — the caller must abort before
capturing anything.

Invariant: runner_sandbox ∩ evidence_dir = ∅ and runner_sandbox ∩
observer-owned = ∅. An observer directory that is inside or equal to the
runner sandbox is not separated.
"""

from __future__ import annotations

import os

# Mirror of Depone capture_bridge.OBSERVER_ID. witnessd is the observer that
# produces this capture; the id is the fixed contract token Depone requires.
OBSERVER_ID = "depone-observer"


class ObserverSeparationError(Exception):
    pass


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_inside_or_equal(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([_norm_path(path), _norm_path(root)]) == _norm_path(
            root
        )
    except ValueError:
        return False


def assert_separated(runner_sandbox: str, out_path: str) -> None:
    """Fail closed unless out_path lives outside the runner sandbox."""
    observer_dir = os.path.dirname(_norm_path(out_path))
    if _is_inside_or_equal(observer_dir, runner_sandbox):
        raise ObserverSeparationError("ERR_OBSERVER_NOT_SEPARATED")
    if _is_inside_or_equal(out_path, runner_sandbox):
        raise ObserverSeparationError("ERR_OBSERVER_NOT_SEPARATED")


def build_observer_capture(
    *,
    command_receipts: list[dict[str, Any]],
    touched_files: list[str],
    allowed_touched_files: list[str],
    test_output: dict[str, Any],
    source_fixture_hash: str = "",
    diff_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an observer_capture matching Depone capture_bridge's required shape.

    Produces exactly the six keys Depone's own observer produces
    (paired_run.build_observer_capture), so Depone's _check_observer_capture_shape
    accepts it. touched_files is emitted exactly as observed — files outside the
    allow-list are not dropped, so the downstream capture manifest fails closed at
    Depone validation (unexpected touched files) rather than being silently
    laundered here. allowed_touched_files is the policy allow-list the observer was
    given; it is carried into the capture manifest (Task 7), not enforced here.
    """
    _ = allowed_touched_files
    if diff_summary is None:
        diff_summary = {"changed_files": list(touched_files)}
    return {
        "observed_by": OBSERVER_ID,
        "source_fixture_hash": source_fixture_hash,
        "diff_summary": diff_summary,
        "touched_files": list(touched_files),
        "test_output": dict(test_output),
        "command_receipts": [dict(receipt) for receipt in command_receipts],
    }
