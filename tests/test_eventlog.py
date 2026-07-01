import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.canonical import canonical_hash


class TestEventLog(unittest.TestCase):
    def test_chain_links_and_genesis_null(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            e1 = log.append({"kind": "witnessd-runlog-event", "event": "team-start"})
            e2 = log.append({"kind": "witnessd-runlog-event", "event": "dispatch"})
            self.assertIsNone(e1["prev_event_hash"])
            self.assertEqual(e2["prev_event_hash"], canonical_hash(e1))

    def test_append_only_no_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            log.append({"kind": "witnessd-runlog-event", "event": "a"})
            self.assertFalse(hasattr(log, "update") or hasattr(log, "delete"))


if __name__ == "__main__":
    unittest.main()
