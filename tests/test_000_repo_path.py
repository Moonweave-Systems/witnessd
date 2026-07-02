from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestRepoPath(unittest.TestCase):
    def test_repo_root_is_importable_from_external_cwd(self) -> None:
        self.assertIn(str(ROOT), sys.path)
