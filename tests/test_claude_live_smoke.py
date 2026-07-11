"""Live smoke test against the real claude CLI (not the fake test binary).

The fake claude binary used by test_claude_opencode_adapter.py always emits
whatever JSONL its script hardcodes regardless of argv, so it cannot catch a
drift between the invocation this adapter builds and what claude actually
needs to emit structured events at all (this is exactly how the missing
--output-format/--verbose flags shipped undetected -- without them claude
prints free text only, with zero events to normalize). This module runs the
adapter against the real, installed, authenticated claude binary to close
that gap.

Skipped unless both:
  - a `claude` binary is on PATH (matches the repo's shutil.which() gate
    convention used for optional tools like openssl), and
  - WITNESSD_LIVE_CLAUDE_SMOKE=1 is set, since this hits a real paid API and
    should never run implicitly in CI or a plain `python3 -m unittest discover`.

Run locally with:
  WITNESSD_LIVE_CLAUDE_SMOKE=1 python3 -m unittest tests.test_claude_live_smoke
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.adapter_run import run_adapter_lane
from witnessd.adapters.claude import run_claude_lane
from witnessd.signing import gen_operator_keypair

_SKIP_REASON = (
    "set WITNESSD_LIVE_CLAUDE_SMOKE=1 with a real claude binary on PATH to run"
)
_LIVE_GATE = (
    shutil.which("claude") is not None
    and os.environ.get("WITNESSD_LIVE_CLAUDE_SMOKE") == "1"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@unittest.skipUnless(_LIVE_GATE, _SKIP_REASON)
class TestClaudeLiveSmoke(unittest.TestCase):
    def test_real_claude_emits_structured_events(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit, create, or delete any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                timeout_seconds=120,
            )

            self.assertEqual(
                res.exit_code,
                0,
                f"real claude rejected the adapter invocation: {res.command_receipts}",
            )
            self.assertTrue(
                res.normalized_events,
                "expected structured JSONL events -- missing --output-format/--verbose?",
            )

    def test_real_claude_edits_a_file_through_run_adapter_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox = root / "sandbox"
            sandbox.mkdir()
            (sandbox / "calc.py").write_text(
                "def average(nums):\n"
                "    return sum(nums) / len(nums)  # bug: empty list -> ZeroDivisionError\n",
                encoding="utf-8",
            )
            _git(["init", "-q"], sandbox)
            _git(["config", "user.email", "smoke@example.invalid"], sandbox)
            _git(["config", "user.name", "smoke"], sandbox)
            _git(["add", "-A"], sandbox)
            _git(["commit", "-qm", "seed"], sandbox)

            keys_dir = root / "keys"
            keys_dir.mkdir()
            private_key, public_key = gen_operator_keypair(str(keys_dir))

            result = run_adapter_lane(
                root=str(sandbox),
                adapter="claude",
                task_id="claude-live-smoke",
                prompt=(
                    "Fix the empty-list bug in calc.py so average([]) returns 0 "
                    "instead of raising ZeroDivisionError. Edit the file directly. "
                    "Minimal change."
                ),
                arm="direct",
                tier="quick",
                is_supported=lambda _model: True,
                budget={"max_tokens": 200000, "max_usd": 1.0, "max_depth": 1},
                sandbox=str(sandbox),
                evidence_dir=str(root / "evidence"),
                state_root=str(root / "state"),
                private_key_path=private_key,
                public_key_path=public_key,
                allowed_touched_files=["calc.py"],
                timeout_seconds=180,
            )

            receipt = result["runner_receipt"]
            self.assertEqual(
                receipt["exit_code"], 0, f"real claude lane failed: {receipt}"
            )
            self.assertTrue(result["normalized_events"], "expected raw claude events")
            # Unlike the state-dir isolation guard (which must produce an exact
            # touched_files == ['calc.py']), claude itself often verifies its
            # edit by running python/ruff, leaving real __pycache__/.ruff_cache
            # artifacts in the sandbox -- that's accurately reported evidence
            # of what actually happened, not witnessd state-dir pollution.
            self.assertIn("calc.py", receipt["touched_files"])
            self.assertNotIn(".witnessd", str(receipt["touched_files"]))


if __name__ == "__main__":
    unittest.main()
