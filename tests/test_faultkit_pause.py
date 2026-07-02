import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.faultkit import pause_race
from witnessd.pause import PAUSE_EVENT, derive_pause_state

_SIDE_EFFECTS = {"spawn", "dispatch", "edit", "commit"}


class TestPauseRace(unittest.TestCase):
    def test_no_side_effect_after_pause(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            records = pause_race(log, run_id="R1")
            self.assertTrue(derive_pause_state(records))
            pause_index = next(i for i, record in enumerate(records) if record.get("event") == PAUSE_EVENT)
            after = records[pause_index + 1 :]
            self.assertFalse(any(record.get("event") in _SIDE_EFFECTS for record in after))


if __name__ == "__main__":
    unittest.main()
