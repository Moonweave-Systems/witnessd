"""Canonical JSON hashing — byte-identical to Depone's canonical_hash.

witnessd emits evidence that Depone re-derives. Both sides MUST hash objects
the same way or every capture-manifest / chain / receipt check fails. This is
the single canonical hashing convention shared across the two repos.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
