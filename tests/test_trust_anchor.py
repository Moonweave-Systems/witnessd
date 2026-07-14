from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from witnessd.trust_anchor import (
    TRUSTED_OBSERVER_PUBLIC_KEY_ENV,
    record_runtime_default_public_key,
    resolve_trust_anchor,
)


class TrustAnchorTests(unittest.TestCase):
    def test_replaced_runtime_default_key_is_operator_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            public_key = Path(tmp) / "operator-ed25519.pub.pem"
            public_key.write_text("runtime key\n", encoding="utf-8")
            record_runtime_default_public_key(public_key)
            public_key.write_text("external operator key\n", encoding="utf-8")

            anchor = resolve_trust_anchor(
                runtime_public_key=public_key,
                environ={TRUSTED_OBSERVER_PUBLIC_KEY_ENV: str(public_key)},
            )

            self.assertEqual(anchor.trust_anchor, "operator-provided")
            self.assertTrue(anchor.independent)


if __name__ == "__main__":
    unittest.main()
