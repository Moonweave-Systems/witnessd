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


class ObserverSeparationError(Exception):
    pass


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


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
