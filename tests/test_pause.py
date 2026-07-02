import os
import tempfile
import unittest

from witnessd.eventlog import EventLog
from witnessd.pause import (
    ERR_WITNESSD_PAUSED,
    PAUSE_EVENT,
    RESUME_EVENT,
    PauseError,
    append_user_pause,
    append_user_resume,
    assert_not_paused,
    derive_pause_state,
)


class TestPause(unittest.TestCase):
    def test_genesis_not_paused(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            self.assertFalse(derive_pause_state(log.read()))

    def test_pause_then_gate_raises(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            record = append_user_pause(log, run_id="R1", source="cli")
            self.assertEqual(record["event"], PAUSE_EVENT)
            self.assertTrue(derive_pause_state(log.read()))
            with self.assertRaises(PauseError) as cm:
                assert_not_paused(log.read())
            self.assertEqual(cm.exception.code, ERR_WITNESSD_PAUSED)

    def test_resume_requires_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            append_user_pause(log, run_id="R1", source="signal")
            with self.assertRaises(PauseError):
                append_user_resume(log, run_id="R1", confirm=False)
            record = append_user_resume(log, run_id="R1", confirm=True)
            self.assertEqual(record["event"], RESUME_EVENT)
            self.assertFalse(derive_pause_state(log.read()))
            assert_not_paused(log.read())


if __name__ == "__main__":
    unittest.main()
