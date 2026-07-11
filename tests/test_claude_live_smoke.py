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

import json
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

    def test_real_claude_accepts_a_valid_model(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                model="sonnet",
                timeout_seconds=120,
            )

            self.assertEqual(res.exit_code, 0)
            self.assertEqual(
                res.model_declaration,
                {
                    "kind": "moonweave-model-declaration",
                    "schema_version": "1.0",
                    "can_change_evidence_verdict": False,
                    "adapter": "claude",
                    "requested_model": "sonnet",
                    "verification_status": "verified",
                    "detail": None,
                },
            )

    def test_real_claude_rejects_an_invalid_model_failclosed(self):
        # Live-verified through the actual adapter path (not a manual
        # terminal check): claude's exit code alone is not a reliable model-
        # rejection signal (observed both 0 and 1 for the same rejection
        # across separate runs), and the "model_not_found" error code lands
        # on the "assistant" message event, not the terminal "result" event.
        # The lane must still fail closed rather than trusting the process
        # exit code.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                model="nonexistent-model-xyz",
                timeout_seconds=120,
            )

            self.assertEqual(res.exit_code, 125)
            self.assertEqual(res.test_output["status"], "failed")
            self.assertEqual(res.model_declaration["verification_status"], "rejected")
            self.assertIsNotNone(res.model_declaration["detail"])

    def test_real_claude_tool_grant_uses_strict_mcp_config_and_allowed_tools(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            source_config = Path(config_dir) / "source-mcp.json"
            source_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "allowed_probe": {
                                "command": "/bin/echo",
                                "args": ["allowed"],
                            },
                            "forbidden_probe": {
                                "command": "/bin/echo",
                                "args": ["forbidden"],
                            },
                        }
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            old_config = os.environ.get("WITNESSD_CLAUDE_MCP_CONFIG")
            os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = str(source_config)
            try:
                res = run_claude_lane(
                    sandbox=sandbox,
                    prompt=(
                        "Try to use the MCP tool named "
                        "mcp__forbidden_probe__forbidden_echo. Do not use Bash. "
                        "If it is unavailable, say unavailable."
                    ),
                    transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                    timeout_seconds=180,
                    tools={
                        "mcp": ["allowed_probe"],
                        "allow": ["mcp__allowed_probe__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                    lane_id="claude-tool-live-smoke",
                )
            finally:
                if old_config is None:
                    os.environ.pop("WITNESSD_CLAUDE_MCP_CONFIG", None)
                else:
                    os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = old_config

            self.assertEqual(res.exit_code, 0, json.dumps(res.command_receipts))
            self.assertIn("--strict-mcp-config", res.invocation)
            generated_config = Path(
                res.invocation[res.invocation.index("--mcp-config") + 1]
            )
            payload = json.loads(generated_config.read_text(encoding="utf-8"))
            self.assertEqual(list(payload["mcpServers"]), ["allowed_probe"])
            self.assertNotIn("forbidden_probe", json.dumps(payload))
            raw = Path(res.raw_events_path).read_text(encoding="utf-8")
            self.assertIn("unavailable", raw.lower())


if __name__ == "__main__":
    unittest.main()
