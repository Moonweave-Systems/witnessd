import unittest

from scripts import revalidate_w11


class TestRevalidateW11(unittest.TestCase):
    def test_revalidate_w11_passes(self):
        self.assertEqual(revalidate_w11.main(), 0)


if __name__ == "__main__":
    unittest.main()
