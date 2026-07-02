"""Capture-manifest builder + prev_capture chain (E2/E8).

witnessd natively emits an agent-fabric capture manifest that Depone's
`validate_capture_manifest` / `verify_capture_chain` re-derive from the bytes.
The manifest hash-binds the observer capture (tamper/stale/unexpected-touched
fail closed at Depone) and links to its predecessor via `prev_capture_hash`, the
canonical hash of the prior manifest. Genesis carries `prev_capture_hash=None`;
the link is committed into this manifest's own canonical hash, so a dropped,
reordered, or tampered predecessor breaks every downstream link.

Runtime stays stdlib-only. The A2 branch uses witnessd's local isolation
verifier replica; same-uid or partial facts never upgrade past A1.
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
EVIDENCE_MODE_CONTEMPORANEOUS = "contemporaneous"
EVIDENCE_MODE_POST_HOC = "post_hoc"
DEFAULT_EPOCH_SECONDS = 300

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
    evidence_mode: str = EVIDENCE_MODE_CONTEMPORANEOUS,
    epoch_seconds: int = DEFAULT_EPOCH_SECONDS,
    monotonic_counter: int = 1,
    parent_attestation_id: str | None = None,
) -> dict[str, Any]:
    """Build a Depone-facing capture manifest from an adapter fixture.

    Without `observer_capture` the manifest is valid but stays A0. With it, the
    observer payload is hash-bound and the manifest records an A1 candidate.
    `isolation` (probed facts) can reach A2 only when it establishes a real
    privilege boundary; otherwise the manifest stays A1.
    """

    if evidence_mode not in {EVIDENCE_MODE_CONTEMPORANEOUS, EVIDENCE_MODE_POST_HOC}:
        raise ValueError("evidence_mode must be 'contemporaneous' or 'post_hoc'")
    if not isinstance(epoch_seconds, int) or epoch_seconds <= 0:
        raise ValueError("epoch_seconds must be a positive integer")
    if not isinstance(monotonic_counter, int) or monotonic_counter <= 0:
        raise ValueError("monotonic_counter must be a positive integer")
    if parent_attestation_id is not None and not (
        isinstance(parent_attestation_id, str)
        and len(parent_attestation_id) == 64
        and all(c in "0123456789abcdef" for c in parent_attestation_id)
    ):
        raise ValueError("parent_attestation_id must be a 64-char sha256 hex string")

    fixture_copy = deepcopy(fixture)
    fixture_hash = canonical_hash(fixture_copy)
    allowed = [item for item in (allowed_touched_files or []) if isinstance(item, str)]
    manifest: dict[str, Any] = {
        "schema_version": CAPTURE_MANIFEST_VERSION,
        "kind": CAPTURE_MANIFEST_KIND,
        "evidence_mode": evidence_mode,
        "epoch_seconds": epoch_seconds,
        "monotonic_counter": monotonic_counter,
        "source_fixture_hash": fixture_hash,
        "fixture": fixture_copy,
        "allowed_touched_files": allowed,
        "prev_capture_hash": prev_capture_hash,
        "required_observer_fields": list(REQUIRED_OBSERVER_FIELDS),
    }
    if parent_attestation_id is not None:
        manifest["parent_attestation_id"] = parent_attestation_id

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
        from witnessd.isolation import verify_isolation_boundary

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
