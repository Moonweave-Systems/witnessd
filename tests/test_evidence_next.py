import unittest

from witnessd.team_ledger import build_evidence_next_verdict


class TestEvidenceNext(unittest.TestCase):
    def test_continue_shape(self):
        verdict = build_evidence_next_verdict()

        self.assertEqual(verdict["command"], "evidence-next")
        self.assertEqual(verdict["decision"], "continue")
        self.assertEqual(verdict["blocking_reasons"], [])

    def test_blocking_reasons_make_blocked_verdict(self):
        verdict = build_evidence_next_verdict(blocking_reasons=["missing receipt"])

        self.assertEqual(verdict["command"], "evidence-next")
        self.assertEqual(verdict["decision"], "blocked")
        self.assertEqual(verdict["blocking_reasons"], ["missing receipt"])


if __name__ == "__main__":
    unittest.main()
