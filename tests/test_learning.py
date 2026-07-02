import unittest

from witnessd.canonical import canonical_hash
from witnessd.learning import (
    ERR_LEARNING_DELTA_UNAPPROVED,
    ERR_LEARNING_PROVENANCE_MISMATCH,
    ERR_LEARNING_PROVENANCE_MISSING,
    LEARNING_DELTA_KIND,
    build_learning_delta,
    validate_learning_delta_provenance,
)


class TestLearning(unittest.TestCase):
    def _capture(self):
        return {
            "kind": "agent-fabric-capture-manifest",
            "assurance": "A1-local-observed",
            "observer_capture": {"observed_by": "depone-observer"},
        }

    def test_valid_delta_provenance_ok(self):
        cap = self._capture()
        approval = {"event": "learning_approval", "event_hash": "abc123"}
        delta = build_learning_delta(
            run_id="R1",
            target="AGENTS.md",
            version=1,
            delta_text="prefer f-strings",
            capture_manifest=cap,
            approval_event_hash="abc123",
            provenance_manifest_hash=canonical_hash(cap),
        )
        self.assertEqual(delta["kind"], LEARNING_DELTA_KIND)
        self.assertEqual(delta["provenance"]["capture_hash"], canonical_hash(cap))
        self.assertEqual(
            validate_learning_delta_provenance(
                delta, committed_captures=[cap], approval_events=[approval]
            ),
            [],
        )

    def test_missing_pointer_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(
            run_id="R1",
            target="AGENTS.md",
            version=1,
            delta_text="x",
            capture_manifest=cap,
            approval_event_hash="abc123",
            provenance_manifest_hash=canonical_hash(cap),
        )
        delta["provenance"]["capture_hash"] = None
        errors = validate_learning_delta_provenance(
            delta, committed_captures=[cap], approval_events=[]
        )
        self.assertIn(ERR_LEARNING_PROVENANCE_MISSING, errors)

    def test_pointer_mismatch_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(
            run_id="R1",
            target="AGENTS.md",
            version=1,
            delta_text="x",
            capture_manifest=cap,
            approval_event_hash="abc123",
            provenance_manifest_hash=canonical_hash(cap),
        )
        other = {"kind": "agent-fabric-capture-manifest", "assurance": "A0-claims-only"}
        errors = validate_learning_delta_provenance(
            delta,
            committed_captures=[other],
            approval_events=[{"event": "learning_approval", "event_hash": "abc123"}],
        )
        self.assertIn(ERR_LEARNING_PROVENANCE_MISMATCH, errors)

    def test_unapproved_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(
            run_id="R1",
            target="AGENTS.md",
            version=1,
            delta_text="x",
            capture_manifest=cap,
            approval_event_hash="abc123",
            provenance_manifest_hash=canonical_hash(cap),
        )
        errors = validate_learning_delta_provenance(
            delta, committed_captures=[cap], approval_events=[]
        )
        self.assertIn(ERR_LEARNING_DELTA_UNAPPROVED, errors)


if __name__ == "__main__":
    unittest.main()
