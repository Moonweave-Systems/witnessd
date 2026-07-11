"""Live smoke test against the real agy (Antigravity) CLI, not the fake test binary.

The fake agy binary used by test_agy_adapter.py never actually writes files
unless explicitly told to (`writes_file=True`), so it cannot catch whether
witnessd's own read-only enforcement holds up against a *real* agy that
decides to edit the sandbox on its own initiative -- which is exactly what
happens live: agy's `--mode plan` is advisory only, and an edit-inducing
prompt makes agy 1.1.1 write to the sandbox even with `--mode plan` set.
This module proves witnessd's own touched_files check (not agy's mode flag)
is what actually keeps the review lane read-only.

Skipped unless both:
  - an `agy` binary is on PATH (matches the repo's shutil.which() gate
    convention used for optional tools like openssl), and
  - WITNESSD_LIVE_AGY_SMOKE=1 is set, since this hits a real paid API and
    should never run implicitly in CI or a plain `python3 -m unittest discover`.

Run locally with:
  WITNESSD_LIVE_AGY_SMOKE=1 python3 -m unittest tests.test_agy_live_smoke
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.adapters.agy import run_agy_review_lane

_SKIP_REASON = "set WITNESSD_LIVE_AGY_SMOKE=1 with a real agy binary on PATH to run"
_LIVE_GATE = (
    shutil.which("agy") is not None and os.environ.get("WITNESSD_LIVE_AGY_SMOKE") == "1"
)


def _seed_repo(sandbox: Path, calc_body: str) -> None:
    (sandbox / "calc.py").write_text(calc_body, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
    subprocess.run(
        ["git", "config", "user.email", "smoke@example.invalid"],
        cwd=sandbox,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=sandbox, check=True)
    subprocess.run(["git", "add", "-A"], cwd=sandbox, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=sandbox, check=True)


_BUGGY_CALC = (
    "def average(nums):\n"
    "    return sum(nums) / len(nums)  # bug: empty list -> ZeroDivisionError\n"
)


@unittest.skipUnless(_LIVE_GATE, _SKIP_REASON)
class TestAgyLiveSmoke(unittest.TestCase):
    def test_real_agy_read_only_prompt_reports_clean_touched_files(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            sandbox_path = Path(sandbox)
            _seed_repo(sandbox_path, _BUGGY_CALC)
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="Review calc.py for bugs. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                timeout_seconds=120,
            )

            self.assertEqual(
                res.exit_code,
                0,
                f"real agy rejected the read-only review lane: {res.command_receipts}",
            )
            self.assertEqual(res.touched_files, [])

    def test_real_agy_edit_inducing_prompt_is_caught_failclosed(self):
        # Deliberately gives agy an edit-inducing prompt to prove witnessd's
        # own enforcement -- not agy's --mode plan -- is what makes this
        # lane read-only. agy's actual choice to write or just describe isn't
        # fully deterministic (observed both outcomes locally with this exact
        # prompt), so this cannot hard-assert one outcome without being flaky.
        # Instead it asserts the *correct* outcome for whichever branch a real
        # agy actually takes: if it wrote anything, witnessd must have caught
        # it fail-closed; if it wrote nothing, there's nothing to catch and
        # the lane simply succeeds clean. Either branch is a valid pass -- the
        # hard-enforcement guarantee itself is already covered deterministically
        # by the fake-binary tests in test_agy_adapter.py; this live case only
        # needs to prove the fail-closed branch actually fires against a real
        # CLI when it does violate read-only.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            sandbox_path = Path(sandbox)
            _seed_repo(sandbox_path, _BUGGY_CALC)
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt=(
                    "Fix the empty-list bug in calc.py directly: edit the file "
                    "so average([]) returns 0. Do it now, don't just describe it."
                ),
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                timeout_seconds=120,
            )

            if res.touched_files:
                self.assertEqual(res.exit_code, 125)
                self.assertEqual(res.test_output["status"], "failed")
                self.assertIn("read-only", res.test_output["summary"])
                self.assertIn("calc.py", res.touched_files)
            else:
                self.assertEqual(res.exit_code, 0)
                self.assertEqual(res.touched_files, [])

    def test_real_agy_requested_model_is_always_reported_unverified(self):
        # agy's --model has no rejection signal at all (live-verified: an
        # invalid model would silently fall back, no error, no exit code
        # change), so a requested model can never be marked "verified" here
        # -- only that a real agy actually accepted the --model flag and ran.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            sandbox_path = Path(sandbox)
            _seed_repo(sandbox_path, _BUGGY_CALC)
            res = run_agy_review_lane(
                sandbox=sandbox,
                prompt="Review calc.py for bugs. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                model="gemini-3.5-flash",
                timeout_seconds=150,
            )

            self.assertIn("--model", res.invocation)
            self.assertEqual(
                res.model_declaration,
                {
                    "kind": "moonweave-model-declaration",
                    "schema_version": "1.0",
                    "can_change_evidence_verdict": False,
                    "adapter": "agy",
                    "requested_model": "gemini-3.5-flash",
                    "verification_status": "requested-unverified",
                    "detail": None,
                },
            )


if __name__ == "__main__":
    unittest.main()
