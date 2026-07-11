"""Live smoke test against the real codex CLI (not the fake test binary).

The fake codex binary used by test_codex_adapter.py ignores unrecognized
argv, so it cannot catch a drift between the invocation this adapter builds
and what the real codex CLI actually accepts (this is exactly how the
--approval-policy/exec-position bug shipped undetected). This module runs
the adapter against the real, installed, authenticated codex binary to
close that gap.

Skipped unless both:
  - a `codex` binary is on PATH (matches the repo's shutil.which() gate
    convention used for optional tools like openssl), and
  - WITNESSD_LIVE_CODEX_SMOKE=1 is set, since this hits a real paid API and
    should never run implicitly in CI or a plain `python3 -m unittest discover`.

Run locally with:
  WITNESSD_LIVE_CODEX_SMOKE=1 python3 -m unittest tests.test_codex_live_smoke
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from witnessd.adapter_run import run_adapter_lane
from witnessd.adapters.codex import run_codex_lane
from witnessd.signing import gen_operator_keypair

_SKIP_REASON = "set WITNESSD_LIVE_CODEX_SMOKE=1 with a real codex binary on PATH to run"
_LIVE_GATE = (
    shutil.which("codex") is not None
    and os.environ.get("WITNESSD_LIVE_CODEX_SMOKE") == "1"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@unittest.skipUnless(_LIVE_GATE, _SKIP_REASON)
class TestCodexLiveSmoke(unittest.TestCase):
    def test_real_codex_accepts_adapter_invocation_and_emits_events(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_codex_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit, create, or delete any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                approval_policy="never",
                allowed_touched_files=["untouched.txt"],
                timeout_seconds=120,
            )

            self.assertEqual(
                res.exit_code,
                0,
                f"real codex rejected the adapter invocation: {res.command_receipts}",
            )
            self.assertTrue(res.normalized_events, "expected at least one raw event")

    def test_real_codex_edits_a_file_through_run_adapter_lane(self):
        # Full-path smoke: run_codex_lane alone (above) exercises the argv
        # fix but calls the real binary directly, bypassing
        # StateNamespace.codex_env()'s isolated CODEX_HOME -- so it cannot
        # catch an auth-seeding regression. Going through run_adapter_lane
        # exercises both the argv fix and the auth-seeding fix together.
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
                adapter="codex",
                task_id="codex-live-smoke",
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
                approval_policy="never",
                timeout_seconds=180,
            )

            receipt = result["runner_receipt"]
            self.assertEqual(
                receipt["exit_code"], 0, f"real codex lane failed: {receipt}"
            )
            self.assertTrue(result["normalized_events"], "expected raw codex events")
            # Exact equality, not just membership: with state_root properly
            # separated from sandbox (above), touched_files must contain only
            # the agent's real edit -- no .witnessd/codex-home noise from
            # codex's own cache/plugin/config writes.
            self.assertEqual(
                receipt["touched_files"],
                ["calc.py"],
                "touched_files must be exactly the agent's edit, no state-dir noise",
            )

    def test_real_codex_accepts_a_valid_model(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_codex_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                approval_policy="never",
                allowed_touched_files=["untouched.txt"],
                model="gpt-5.5",
                timeout_seconds=120,
            )

            self.assertEqual(res.exit_code, 0)
            self.assertEqual(
                res.model_declaration,
                {
                    "kind": "moonweave-model-declaration",
                    "schema_version": "1.0",
                    "can_change_evidence_verdict": False,
                    "adapter": "codex",
                    "requested_model": "gpt-5.5",
                    "verification_status": "verified",
                    "detail": None,
                },
            )

    def test_real_codex_rejects_an_invalid_model_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
        ):
            res = run_codex_lane(
                sandbox=sandbox,
                prompt="Reply with the single word OK. Do not edit any files.",
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                sandbox_mode="workspace-write",
                approval_policy="never",
                allowed_touched_files=["untouched.txt"],
                model="nonexistent-model-xyz",
                timeout_seconds=120,
            )

            self.assertEqual(res.exit_code, 125)
            self.assertEqual(res.test_output["status"], "failed")
            self.assertEqual(res.model_declaration["verification_status"], "rejected")
            self.assertIsNotNone(res.model_declaration["detail"])

    def test_real_codex_tool_grant_exposes_only_allowed_mcp_config(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as codex_home,
        ):
            ambient = Path(home) / ".codex"
            ambient.mkdir()
            real_auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
            if real_auth.exists():
                (Path(codex_home) / "auth.json").write_bytes(real_auth.read_bytes())
                (Path(codex_home) / "auth.json").chmod(0o600)
            ambient_config = ambient / "config.toml"
            ambient_config.write_text(
                "[mcp_servers.allowed_probe]\n"
                'command = "/bin/echo"\n'
                'args = ["allowed"]\n'
                "\n"
                "[mcp_servers.forbidden_probe]\n"
                'command = "/bin/echo"\n'
                'args = ["forbidden"]\n',
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": home,
                "CODEX_HOME": codex_home,
                "PYTHONNOUSERSITE": "1",
            }

            res = run_codex_lane(
                sandbox=sandbox,
                prompt=(
                    "Try to use the MCP tool named "
                    "mcp__forbidden_probe__forbidden_echo. Do not use shell. "
                    "If it is unavailable, say unavailable."
                ),
                transcript_path=str(Path(evidence) / "events.raw.jsonl"),
                sandbox_mode="read-only",
                approval_policy="never",
                timeout_seconds=180,
                env=env,
                tools={
                    "mcp": ["allowed_probe"],
                    "allow": ["mcp__allowed_probe__allowed_echo"],
                },
                role_id="runner",
                role_capability="execute",
                lane_id="codex-tool-live-smoke",
            )

            config_text = (Path(codex_home) / "config.toml").read_text(
                encoding="utf-8"
            )
            self.assertIn("[mcp_servers.allowed_probe]", config_text)
            self.assertNotIn("forbidden_probe", config_text)
            self.assertEqual(res.exit_code, 0, json.dumps(res.command_receipts))
            self.assertEqual(
                res.tool_declaration["usage_verification_status"], "enforced-only"
            )
            raw = Path(res.raw_events_path).read_text(encoding="utf-8")
            self.assertIn("unavailable", raw.lower())


if __name__ == "__main__":
    unittest.main()
