import unittest
from witnessd.status import render_status, STATUS_DOMAIN


class TestStatus(unittest.TestCase):
    def test_output_in_enum_domain(self):
        self.assertIn(render_status(pending=3, verdict=None), STATUS_DOMAIN)

    def test_no_success_theater(self):
        for s in STATUS_DOMAIN:
            self.assertNotIn("VERIFIED", s)
            self.assertNotIn("COMPLETE", s)

    def test_pending_shown_until_depone(self):
        self.assertIn("evidence-pending", render_status(pending=3, verdict=None))


if __name__ == "__main__":
    unittest.main()
