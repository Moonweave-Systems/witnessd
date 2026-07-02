import unittest


class TestRevalidateW10(unittest.TestCase):
    def test_w10_live_fixture_revalidates(self):
        from scripts import revalidate_w10

        self.assertEqual(revalidate_w10.main(), 0)


if __name__ == "__main__":
    unittest.main()
