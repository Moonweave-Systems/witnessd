import json
import os
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain

from witnessd.faultkit import crash_mid_toolcall
from witnessd.runlog import verify_runlog
from witnessd.session import SessionStore

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "w2"


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestW2Monotonicity(unittest.TestCase):
    def test_w2_a2_passes_w1_validators(self):
        manifest = json.loads((FIX / "capture-manifest-a2.json").read_text())

        self.assertEqual(validate_capture_manifest(manifest), [])
        self.assertEqual(verify_capture_chain([manifest])["decision"], "pass")

    def test_session_store_preserves_resume_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.save(
                "R1",
                {
                    "run_state": "evidence-pending",
                    "tool_call_cursor": 3,
                    "last_event_hash": "ab",
                },
            )

            resumed = store.resume("R1")

            self.assertEqual(resumed["tool_call_cursor"], 3)
            self.assertEqual(resumed["run_state"], "evidence-pending")

    def test_durable_resume_fixture_continues_same_runlog_chain(self):
        before = _jsonl(FIX / "durable-resume" / "runlog-before.jsonl")
        after = _jsonl(FIX / "durable-resume" / "runlog-after.jsonl")
        session = json.loads(
            (FIX / "durable-resume" / "session.json").read_text(encoding="utf-8")
        )

        self.assertEqual(verify_runlog(before), {"ok": True, "broken_at": None})
        self.assertEqual(verify_runlog(after), {"ok": True, "broken_at": None})
        self.assertEqual(after[: len(before)], before)
        self.assertEqual(after[len(before)]["prev_event_hash"], before[-1]["event_hash"])
        self.assertEqual({record["run_id"] for record in after}, {session["run_id"]})
        self.assertEqual(session["last_event_hash"], after[-1]["event_hash"])

    def test_crash_mid_toolcall_resume_is_evidence_pending_without_reapply(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.path.join(tmp, "before.jsonl")
            after = os.path.join(tmp, "after.jsonl")
            session = os.path.join(tmp, "session.json")

            state = crash_mid_toolcall(
                runlog_before_path=before,
                runlog_after_path=after,
                session_path=session,
            )

            self.assertEqual(state["run_state"], "evidence-pending")
            self.assertEqual(state["idempotency_reapplied"], 0)
            self.assertEqual(state["tool_call_cursor"], 1)


if __name__ == "__main__":
    unittest.main()
