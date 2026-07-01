import unittest

from witnessd.canonical import canonical_hash
from depone.agent_fabric.claim_gate import canonical_hash as depone_hash


class TestCanonical(unittest.TestCase):
    def test_matches_depone(self):
        obj = {"b": 1, "a": [3, 2], "nested": {"z": "x"}}
        self.assertEqual(canonical_hash(obj), depone_hash(obj))

    def test_key_order_independent(self):
        self.assertEqual(
            canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1})
        )


if __name__ == "__main__":
    unittest.main()
