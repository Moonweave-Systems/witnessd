import unittest

from witnessd.team_ledger import classify_lane_kind


class TestLaneKind(unittest.TestCase):
    def test_no_touched_is_read_only(self):
        self.assertEqual(classify_lane_kind(touched_files=[]), "read-only")

    def test_touched_files_is_write(self):
        self.assertEqual(classify_lane_kind(touched_files=["pkg/a.py"]), "write")

    def test_empty_non_string_entries_do_not_make_write_lane(self):
        self.assertEqual(classify_lane_kind(touched_files=["", None]), "read-only")


if __name__ == "__main__":
    unittest.main()
