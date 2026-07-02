import os
import tempfile
import unittest

from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle

from witnessd.canonical import canonical_hash
from witnessd.eventlog import EventLog
from witnessd.learning import build_learning_delta, promote_learning_delta
from witnessd.signing import gen_operator_keypair


class TestLearningPromote(unittest.TestCase):
    def test_promoted_delta_ingestible(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            cap = {
                "kind": "agent-fabric-capture-manifest",
                "assurance": "A1-local-observed",
                "observer_capture": {"observed_by": "depone-observer"},
            }
            approval = log.append(
                {"kind": "witnessd-runlog-event", "event": "learning_approval", "run_id": "R1"}
            )
            delta = build_learning_delta(
                run_id="R1",
                target="AGENTS.md",
                version=1,
                delta_text="prefer f-strings",
                capture_manifest=cap,
                approval_event_hash=approval["event_hash"],
                provenance_manifest_hash=canonical_hash(cap),
            )

            result = promote_learning_delta(
                delta,
                log=log,
                run_id="R1",
                priv=priv,
                pub=pub,
                committed_captures=[cap],
                approval_events=[approval],
                evidence_dir=d,
            )

            self.assertTrue(result["promoted"])
            verdict = ingest_signed_evidence_bundle(
                result["bundle"],
                pub,
                result["artifact_paths"],
                otel_spans=result["bundle"]["otel_spans"],
            )
            self.assertTrue(verdict["signature_verified"])
            self.assertEqual(verdict["decision"], "pass")

    def test_unapproved_delta_refused(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            cap = {"kind": "agent-fabric-capture-manifest", "assurance": "A1-local-observed"}
            delta = build_learning_delta(
                run_id="R1",
                target="AGENTS.md",
                version=1,
                delta_text="x",
                capture_manifest=cap,
                approval_event_hash="nope",
                provenance_manifest_hash=canonical_hash(cap),
            )

            result = promote_learning_delta(
                delta,
                log=log,
                run_id="R1",
                priv=priv,
                pub=pub,
                committed_captures=[cap],
                approval_events=[],
                evidence_dir=d,
            )

            self.assertFalse(result["promoted"])
            self.assertIn("ERR_LEARNING_DELTA_UNAPPROVED", result["errors"])
            self.assertTrue(
                any(
                    record.get("event") == "learning_delta" and record.get("error_code")
                    for record in log.read()
                )
            )


if __name__ == "__main__":
    unittest.main()
