import multiprocessing
import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.runlog import verify_runlog


def _append_many(path: str, worker_id: int, count: int) -> None:
    log = EventLog(path)
    for index in range(count):
        log.append(
            {
                "kind": "witnessd-runlog-event",
                "event": "parallel-append",
                "worker_id": worker_id,
                "index": index,
            }
        )


class TestEventLog(unittest.TestCase):
    def test_chain_links_and_genesis_null(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            e1 = log.append({"kind": "witnessd-runlog-event", "event": "team-start"})
            e2 = log.append({"kind": "witnessd-runlog-event", "event": "dispatch"})
            self.assertIsNone(e1["prev_event_hash"])
            self.assertEqual(e2["prev_event_hash"], e1["event_hash"])

    def test_append_only_no_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            log.append({"kind": "witnessd-runlog-event", "event": "a"})
            self.assertFalse(hasattr(log, "update") or hasattr(log, "delete"))

    def test_parallel_appends_keep_hash_chain(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runlog.jsonl")
            processes = [
                multiprocessing.Process(target=_append_many, args=(path, worker_id, 20))
                for worker_id in range(6)
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)

            self.assertEqual([process.exitcode for process in processes], [0] * len(processes))
            records = EventLog(path).read()
            self.assertEqual(len(records), 120)
            self.assertEqual([record["seq"] for record in records], list(range(120)))
            self.assertEqual(verify_runlog(records), {"ok": True, "broken_at": None})


if __name__ == "__main__":
    unittest.main()
