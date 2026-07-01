"""Capture-manifest builder + prev_capture chain (E2/E8).

witnessd natively emits an agent-fabric capture manifest that Depone's
`validate_capture_manifest` / `verify_capture_chain` re-derive from the bytes.
The manifest hash-binds the observer capture (tamper/stale/unexpected-touched
fail closed at Depone) and links to its predecessor via `prev_capture_hash`, the
canonical hash of the prior manifest. Genesis carries `prev_capture_hash=None`;
the link is committed into this manifest's own canonical hash, so a dropped,
reordered, or tampered predecessor breaks every downstream link.

Runtime stays stdlib-only. The A2 branch alone imports Depone's
`verify_isolation_boundary` to normalize probed isolation facts into the stored
boundary object exactly as Depone would; same-uid or partial facts never upgrade
past A1.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from witnessd.canonical import canonical_hash

CAPTURE_MANIFEST_VERSION = "1.0"
CAPTURE_MANIFEST_KIND = "agent-fabric-capture-manifest"
ASSURANCE_A0 = "A0-claims-only"
ASSURANCE_A1 = "A1-local-observed"
ASSURANCE_A2 = "A2-isolated-observed"
DECISION_CLAIMS_ONLY = "claims-only"
DECISION_OBSERVED = "observed-local-capture"
DECISION_ISOLATED = "isolated-observed"

# sorted(REQUIRED_OBSERVER_FIELDS) from Depone capture_bridge — emitted verbatim
# so the byte layout matches what Depone's own builder produces.
REQUIRED_OBSERVER_FIELDS = [
    "command_receipts",
    "diff_summary",
    "observed_by",
    "source_fixture_hash",
    "test_output",
    "touched_files",
]


def build_capture_manifest(
    fixture: dict[str, Any],
    *,
    observer_capture: dict[str, Any] | None = None,
    allowed_touched_files: list[str] | None = None,
    prev_capture_hash: str | None = None,
    isolation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Depone-facing capture manifest from an adapter fixture.

    Without `observer_capture` the manifest is valid but stays A0. With it, the
    observer payload is hash-bound and the manifest records an A1 candidate.
    `isolation` (probed facts) can reach A2 only when it establishes a real
    privilege boundary; otherwise the manifest stays A1.
    """

    fixture_copy = deepcopy(fixture)
    fixture_hash = canonical_hash(fixture_copy)
    allowed = [item for item in (allowed_touched_files or []) if isinstance(item, str)]
    manifest: dict[str, Any] = {
        "schema_version": CAPTURE_MANIFEST_VERSION,
        "kind": CAPTURE_MANIFEST_KIND,
        "source_fixture_hash": fixture_hash,
        "fixture": fixture_copy,
        "allowed_touched_files": allowed,
        "prev_capture_hash": prev_capture_hash,
        "required_observer_fields": list(REQUIRED_OBSERVER_FIELDS),
    }

    if observer_capture is None:
        manifest.update(
            {
                "assurance": ASSURANCE_A0,
                "decision": DECISION_CLAIMS_ONLY,
                "observer_capture": None,
                "observer_capture_hash": None,
            }
        )
        return manifest

    observed = deepcopy(observer_capture)
    if not observed.get("source_fixture_hash"):
        observed["source_fixture_hash"] = fixture_hash
    manifest.update(
        {
            "assurance": ASSURANCE_A1,
            "decision": DECISION_OBSERVED,
            "observer_capture": observed,
            "observer_capture_hash": canonical_hash(observed),
        }
    )

    if isolation is not None:
        from depone.agent_fabric.isolation import verify_isolation_boundary

        verified = verify_isolation_boundary(isolation)
        if verified.get("boundary") is True:
            manifest.update(
                {
                    "assurance": ASSURANCE_A2,
                    "decision": DECISION_ISOLATED,
                    "isolation": verified,
                    "isolation_hash": canonical_hash(verified),
                }
            )
    return manifest
