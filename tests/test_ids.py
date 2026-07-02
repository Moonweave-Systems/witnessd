import unittest
from witnessd.ids import new_run_id


class TestIds(unittest.TestCase):
    def test_shape_and_alphabet(self):
        rid = new_run_id()
        self.assertEqual(len(rid), 26)
        self.assertTrue(all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in rid))

    def test_monotone_prefix_sorts_by_time(self):
        import time

        a = new_run_id()
        time.sleep(0.002)
        b = new_run_id()
        self.assertLess(a, b)  # 시간 정렬 (상위 timestamp 비트)

    def test_unique(self):
        self.assertEqual(len({new_run_id() for _ in range(1000)}), 1000)
