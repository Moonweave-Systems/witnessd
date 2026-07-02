import os
import tempfile
import unittest

from witnessd.session import SessionResumeError, SessionStore


class TestSession(unittest.TestCase):
    def test_save_resume_preserves_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            store.save(
                "R1",
                {
                    "last_prompt": "go",
                    "tool_call_cursor": 42,
                    "worktree": "/w/L1",
                    "last_seq": 7,
                    "last_event_hash": "ab",
                },
            )

            state = store.resume("R1")

            self.assertEqual(state["tool_call_cursor"], 42)
            self.assertEqual(state["last_seq"], 7)
            self.assertEqual(state["last_event_hash"], "ab")

    def test_atomic_no_torn_write(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            store.save("R1", {"tool_call_cursor": 1})
            store.save("R1", {"tool_call_cursor": 2})

            self.assertEqual(store.resume("R1")["tool_call_cursor"], 2)
            run_dir = os.path.join(d, "runs", "R1")
            self.assertEqual(
                [name for name in os.listdir(run_dir) if name.endswith(".tmp")],
                [],
            )

    def test_unreadable_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            run_dir = os.path.join(d, "runs", "R1")
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "session.json"), "w", encoding="utf-8") as f:
                f.write("{ not json")

            with self.assertRaises(SessionResumeError):
                store.resume("R1")


if __name__ == "__main__":
    unittest.main()
