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
    "set WITNESSD_LIVE_CLAUDE_SMOKE=1 with a real authenticated claude binary on PATH to run"
)


def _claude_auth_ready() -> bool:
    if shutil.which("claude") is None:
        return False
    if os.environ.get("WITNESSD_LIVE_CLAUDE_SMOKE") != "1":
        return False
    try:
        completed = subprocess.run(
            ["claude", "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    return payload.get("loggedIn") is True


_LIVE_GATE = (
    _claude_auth_ready()
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_neutral_mcp_server(path: Path) -> None:
    path.write_text(
        r'''
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _log(payload: dict) -> None:
    log_path = os.environ.get("R4_MCP_LOG")
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")


def _reply(request: dict, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "allowed_echo",
        "description": "Approved neutral test echo.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "neutral_check",
        "description": "Neutral test tool used to prove PreToolUse denial.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]


for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        _log({"method": method})
        _reply(
            request,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "neutral_probe", "version": "1.0"},
            },
        )
    elif method == "notifications/initialized":
        _log({"method": method})
    elif method == "tools/list":
        _log({"method": method})
        _reply(request, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        _log({"method": method, "name": name})
        text = ""
        arguments = params.get("arguments")
        if isinstance(arguments, dict) and isinstance(arguments.get("text"), str):
            text = arguments["text"]
        _reply(request, {"content": [{"type": "text", "text": f"{name}:{text}"}]})
    else:
        _log({"method": method})
        _reply(request, {})
'''.lstrip(),
        encoding="utf-8",
    )


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


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

    def test_real_claude_pretooluse_pep_allows_and_denies_before_mcp_call(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as evidence,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            mcp_server = Path(config_dir) / "neutral_mcp.py"
            mcp_log = Path(config_dir) / "mcp.jsonl"
            _write_neutral_mcp_server(mcp_server)
            source_config = Path(config_dir) / "source-mcp.json"
            source_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "neutral_probe": {
                                "command": "/usr/bin/python3",
                                "args": [str(mcp_server)],
                                "env": {"R4_MCP_LOG": str(mcp_log)},
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
                allowed = run_claude_lane(
                    sandbox=sandbox,
                    prompt=(
                        "This is an approved test. Use the MCP tool "
                        "mcp__neutral_probe__allowed_echo with text 'allowed-live'. "
                        "Do not use Bash."
                    ),
                    transcript_path=str(Path(evidence) / "allowed.raw.jsonl"),
                    timeout_seconds=180,
                    tools={
                        "mcp": ["neutral_probe"],
                        "allow": ["mcp__neutral_probe__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                    lane_id="claude-r4-allowed-live",
                )
                self.assertEqual(
                    allowed.exit_code, 0, json.dumps(allowed.command_receipts)
                )
                self.assertTrue(
                    any(
                        item.get("canonical_tool_name")
                        == "mcp__neutral_probe__allowed_echo"
                        and item.get("decision") == "allow"
                        for item in allowed.tool_decision_advisory["decisions"]
                    ),
                    allowed.tool_decision_advisory,
                )
                self.assertTrue(
                    any(
                        item.get("canonical_tool_name")
                        == "mcp__neutral_probe__allowed_echo"
                        and item.get("decision") == "allow"
                        for item in allowed.tool_decision_receipts["decisions"]
                    ),
                    allowed.tool_decision_receipts,
                )
                self.assertFalse(
                    [
                        item
                        for item in allowed.tool_decision_advisory["decisions"]
                        if not str(item.get("canonical_tool_name", "")).startswith(
                            "mcp__"
                        )
                        and item.get("decision") == "deny"
                    ],
                    allowed.tool_decision_advisory,
                )
                self.assertTrue(
                    any(
                        item.get("method") == "tools/call"
                        and item.get("name") == "allowed_echo"
                        for item in _jsonl(mcp_log)
                    ),
                    mcp_log.read_text(encoding="utf-8") if mcp_log.exists() else "",
                )

                mcp_log.write_text("", encoding="utf-8")
                denied = run_claude_lane(
                    sandbox=sandbox,
                    prompt=(
                        "This is an approved test of a tool policy gate. Try to use "
                        "the MCP tool mcp__neutral_probe__neutral_check with text "
                        "'deny-live'. Do not refuse on safety grounds. Do not use Bash. "
                        "If the tool is blocked, report the blocking reason."
                    ),
                    transcript_path=str(Path(evidence) / "denied.raw.jsonl"),
                    timeout_seconds=180,
                    tools={
                        "mcp": ["neutral_probe"],
                        "allow": ["mcp__neutral_probe__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                    lane_id="claude-r4-denied-live",
                )
            finally:
                if old_config is None:
                    os.environ.pop("WITNESSD_CLAUDE_MCP_CONFIG", None)
                else:
                    os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = old_config

            self.assertEqual(denied.exit_code, 0, json.dumps(denied.command_receipts))
            self.assertTrue(
                any(
                    item.get("canonical_tool_name")
                    == "mcp__neutral_probe__neutral_check"
                    and item.get("decision") == "deny"
                    for item in denied.tool_decision_advisory["decisions"]
                ),
                denied.tool_decision_advisory,
            )
            self.assertTrue(
                any(
                    item.get("canonical_tool_name")
                    == "mcp__neutral_probe__neutral_check"
                    and item.get("decision") == "deny"
                    for item in denied.tool_decision_receipts["decisions"]
                ),
                denied.tool_decision_receipts,
            )
            self.assertFalse(
                [
                    item
                    for item in denied.tool_decision_advisory["decisions"]
                    if not str(item.get("canonical_tool_name", "")).startswith("mcp__")
                    and item.get("decision") == "deny"
                ],
                denied.tool_decision_advisory,
            )
            self.assertFalse(
                any(
                    item.get("method") == "tools/call"
                    and item.get("name") == "neutral_check"
                    for item in _jsonl(mcp_log)
                ),
                mcp_log.read_text(encoding="utf-8") if mcp_log.exists() else "",
            )


if __name__ == "__main__":
    unittest.main()
