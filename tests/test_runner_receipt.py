import copy
import unittest

from depone.agent_fabric.paired_run import VALID_RUNNERS, validate_runner_receipt

from witnessd.canonical import canonical_hash
from witnessd.receipt import build_runner_receipt


def _make_receipt() -> dict:
    return build_runner_receipt(
        task_id="w1-lane",
        worktree="/tmp/sandbox",
        invocation=["sh", "-c", "echo hi"],
        transcript_path="/tmp/evidence/verify.log",
        exit_code=0,
        touched_files=["f.txt"],
        started_at="2026-07-01T00:00:00Z",
        ended_at="2026-07-01T00:00:01Z",
    )


class TestRunnerReceipt(unittest.TestCase):
    def test_depone_validates(self):
        self.assertEqual(validate_runner_receipt(_make_receipt()), [])

    def test_runner_kind_manual(self):
        receipt = _make_receipt()
        self.assertEqual(receipt["runner_kind"], "manual")
        self.assertIn(receipt["runner_kind"], VALID_RUNNERS)

    def test_self_hash_excludes_source_hashes(self):
        receipt = _make_receipt()
        without = {k: v for k, v in receipt.items() if k != "source_hashes"}
        self.assertEqual(receipt["source_hashes"]["receipt"], canonical_hash(without))

    def test_self_hash_stable_under_copy(self):
        receipt = _make_receipt()
        stripped = copy.deepcopy(receipt)
        stripped.pop("source_hashes")
        self.assertEqual(canonical_hash(stripped), receipt["source_hashes"]["receipt"])
