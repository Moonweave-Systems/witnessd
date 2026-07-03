import os
import unittest

from witnessd.process_identity import (
    pid_identity_matches,
    process_state,
    read_pid_start_time,
)


class TestProcessIdentity(unittest.TestCase):
    def test_live_pid_identity_binds_to_current_backend_start_time(self):
        pid = os.getpid()
        start_time = read_pid_start_time(pid)

        self.assertIsInstance(start_time, str)
        self.assertNotEqual(start_time.strip(), "")
        self.assertTrue(pid_identity_matches(pid, start_time))

    def test_live_pid_state_is_available_when_process_exists(self):
        state = process_state(os.getpid())

        self.assertIsInstance(state, str)
        self.assertNotEqual(state.strip(), "")


if __name__ == "__main__":
    unittest.main()
