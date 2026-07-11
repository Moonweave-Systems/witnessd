from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if os.environ.get("WITNESSD_DEPONE_ROOT"):
    depone_candidates = [Path(os.environ["WITNESSD_DEPONE_ROOT"])]
else:
    depone_candidates = [ROOT.parent / "depone", ROOT.parent / "Depone"]
for depone_root in depone_candidates:
    if (depone_root / "depone").is_dir():
        if str(depone_root) not in sys.path:
            sys.path.insert(0, str(depone_root))
        break
else:
    raise RuntimeError(
        "witnessd tests require WITNESSD_DEPONE_ROOT or a sibling depone/Depone checkout"
    )
