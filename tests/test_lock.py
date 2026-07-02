import json
import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.lock import ClaimConflictError, OwnershipRegistry


class TestLock(unittest.TestCase):
    def _reg(self, directory: str) -> OwnershipRegistry:
        return OwnershipRegistry(EventLog(os.path.join(directory, "runlog.jsonl")))

    def test_claim_returns_allowed_touched_files(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self._reg(directory)

            allowed = registry.claim(lane_id="lane-a", region=["pkg/b.py", "pkg/a.py"])

            self.assertEqual(allowed, ["pkg/a.py", "pkg/b.py"])

    def test_claim_rejects_absolute_region_path(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self._reg(directory)

            with self.assertRaisesRegex(ValueError, "ERR_REGION_INVALID_PATH"):
                registry.claim(lane_id="lane-a", region=["/tmp/outside.py"])

    def test_conflicting_region_second_claim_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self._reg(directory)
            registry.claim(lane_id="lane-a", region=["pkg/a.py"])

            with self.assertRaises(ClaimConflictError):
                registry.claim(lane_id="lane-b", region=["pkg/a.py"])

    def test_conflict_emits_claim_conflict_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "runlog.jsonl")
            registry = OwnershipRegistry(EventLog(path))
            registry.claim(lane_id="lane-a", region=["pkg/a.py"])

            with self.assertRaises(ClaimConflictError):
                registry.claim(lane_id="lane-b", region=["pkg/a.py"])

            with open(path, encoding="utf-8") as handle:
                events = [json.loads(line)["event"] for line in handle]
            self.assertIn("claim-conflict", events)

    def test_conflict_does_not_partially_claim_non_conflicting_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self._reg(directory)
            registry.claim(lane_id="lane-a", region=["pkg/a.py"])

            with self.assertRaises(ClaimConflictError):
                registry.claim(lane_id="lane-b", region=["pkg/a.py", "pkg/b.py"])

            self.assertIsNone(registry.owner_of("pkg/b.py"))

    def test_release_then_reclaim_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self._reg(directory)
            registry.claim(lane_id="lane-a", region=["pkg/a.py"])

            registry.release(lane_id="lane-a")
            registry.claim(lane_id="lane-b", region=["pkg/a.py"])

            self.assertEqual(registry.owner_of("pkg/a.py"), "lane-b")


if __name__ == "__main__":
    unittest.main()
