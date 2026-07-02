"""Demo W3's split-claim guard and merge-receipt gate.

The demo runs two lanes that both claim ``pkg/shared.py``. witnessd rejects the
second claim and records ``claim-conflict`` in the runlog. Depone revalidation
for the committed overlap fixture remains in ``scripts/revalidate_w3.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from witnessd.fanin import run_team  # noqa: E402
from witnessd.signing import gen_operator_keypair  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "w3"


def _git(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _seed_repo(repo: Path) -> str:
    repo.mkdir()
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "w@x.invalid"])
    _git(repo, ["config", "user.name", "w3"])
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-qm", "seed"])
    return _git(repo, ["rev-parse", "HEAD"])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        base_commit = _seed_repo(repo)
        keys = root / "keys"
        keys.mkdir()
        private_key_path, public_key_path = gen_operator_keypair(str(keys))
        result = run_team(
            [
                {
                    "lane_id": "lane-a",
                    "region": ["pkg/shared.py"],
                    "commands": [
                        ["sh", "-c", "mkdir -p pkg && echo a > pkg/shared.py"]
                    ],
                },
                {
                    "lane_id": "lane-b",
                    "region": ["pkg/shared.py"],
                    "commands": [
                        ["sh", "-c", "mkdir -p pkg && echo b > pkg/shared.py"]
                    ],
                },
            ],
            repo_root=str(repo),
            out_dir=str(root / "evidence"),
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            base_commit=base_commit,
        )
        conflict_seen = any(
            event.get("event") == "claim-conflict" for event in result["runlog"]
        )
        overlap_ledger = json.loads(
            (FIXTURES / "team-ledger-overlap.json").read_text(encoding="utf-8")
        )
        summary = {
            "claim_conflict_seen": conflict_seen,
            "accepted_lanes": [
                lane["lane_id"] for lane in result["ledger"]["lanes"]
            ],
            "overlap_fixture_lanes": [
                lane["lane_id"] for lane in overlap_ledger["lanes"]
            ],
            "overlap_fixture_requires_depone_revalidation": True,
        }
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
