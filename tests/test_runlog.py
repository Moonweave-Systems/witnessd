import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.runlog import append_runlog, verify_runlog
from witnessd.canonical import canonical_hash


class TestRunlog(unittest.TestCase):
    def test_record_shape_and_hash(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r = append_runlog(
                log, run_id="R1", event="spawn", payload={"lane_id": "L1"}
            )
            for k in (
                "schema_version",
                "kind",
                "run_id",
                "seq",
                "event",
                "error_code",
                "ts_wall",
                "ts_monotonic",
                "payload",
                "prev_event_hash",
                "event_hash",
            ):
                self.assertIn(k, r)
            self.assertEqual(r["kind"], "witnessd-runlog-event")
            self.assertIsNone(r["prev_event_hash"])
            self.assertEqual(
                r["event_hash"],
                canonical_hash({k: v for k, v in r.items() if k != "event_hash"}),
            )

    def test_chain_links_prev_to_event_hash(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r1 = append_runlog(log, run_id="R1", event="spawn")
            r2 = append_runlog(log, run_id="R1", event="heartbeat")
            self.assertEqual(r2["prev_event_hash"], r1["event_hash"])
            self.assertEqual(verify_runlog([r1, r2]), {"ok": True, "broken_at": None})

    def test_tamper_detected(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r1 = append_runlog(log, run_id="R1", event="spawn")
            r2 = append_runlog(log, run_id="R1", event="heartbeat")
            r2["payload"] = {"forged": True}  # tamper without re-hashing
            self.assertEqual(verify_runlog([r1, r2])["ok"], False)


if __name__ == "__main__":
    unittest.main()
