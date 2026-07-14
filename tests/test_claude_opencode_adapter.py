import hashlib
import json
import os
import pathlib
import shlex
import stat
import subprocess
import tempfile
import unittest

from depone.agent_fabric.paired_run import validate_runner_receipt

from witnessd.adapters.claude import (
    ClaudeAdapterError,
    _build_claude_tool_decision_receipts,
    _claude_pep_args,
    run_claude_lane,
)
from witnessd.adapters.opencode import OpenCodeAdapterError, run_opencode_lane


def _fake_cli(directory: str, name: str) -> str:
    path = pathlib.Path(directory) / name
    path.write_text(
        "#!/bin/sh\necho ran >&2\nexit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_jsonl(directory: str) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"type":"session.started","session_id":"S1"}\'\n'
        'printf \'%s\\n\' \'{"type":"assistant.message","message_id":"M1","text":"done"}\'\n'
        'printf \'%s\\n\' \'{"type":"tool.completed","tool_name":"Bash","tool_use_id":"T1"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_model_probe(directory: str, *, reject_model: str | None = None) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/bin/sh\n"
        "model=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--model" ]; then shift; model="$1"; shift; continue; fi\n'
        "  shift\n"
        "done\n"
        f'if [ "$model" = "{reject_model}" ]; then\n'
        '  printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":true,"error":"model_not_found",'
        '"result":"model rejected"}\'\n'
        "else\n"
        '  printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":false,"result":"OK"}\'\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _execute_pep(
    task_dir: pathlib.Path,
    tool_name: str,
    *,
    allow: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    settings_path = task_dir / "claude-settings.json"
    if not settings_path.exists():
        _claude_pep_args(
            tools={"mcp": [], "allow": list(allow or [])},
            task_dir=task_dir,
            role_id="runner",
            role_capability="execute",
            lane_id="execute-1",
        )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    return subprocess.run(
        shlex.split(command),
        input=json.dumps({"tool_name": tool_name}),
        text=True,
        capture_output=True,
        check=False,
    )


class TestClaudeOpenCodeAdapter(unittest.TestCase):
    def _check(self, res, cli_name: str, worktree: str) -> None:
        self.assertEqual(res.runner_kind, "manual")
        self.assertTrue(any(cli_name in token for token in res.invocation))
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.test_output, {"status": "not-run"})
        receipt = res.to_runner_receipt(
            arm="direct",
            task_id="t",
            worktree=worktree,
            started_at="2026-07-01T00:00:00Z",
            ended_at="2026-07-01T00:00:01Z",
        )
        self.assertEqual(validate_runner_receipt(receipt), [])
        self.assertEqual(receipt["runner_kind"], "manual")

    def test_claude(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_cli(bindir, "claude"),
                transcript_path=str(pathlib.Path(bindir) / "claude.txt"),
            )

            self._check(res, "claude", sandbox)
            self.assertIn("-p", res.invocation)
            # Live-verified: claude rejects --output-format stream-json without
            # --verbose ("Error: When using --print, --output-format=stream-json
            # requires --verbose"), and without --output-format at all it never
            # emits structured JSONL, only free text -- so normalize_claude_
            # jsonl_events has nothing to parse. Both flags are required.
            self.assertIn("--output-format", res.invocation)
            self.assertEqual(
                res.invocation[res.invocation.index("--output-format") + 1],
                "stream-json",
            )
            self.assertIn("--verbose", res.invocation)

    def test_claude_jsonl_normalizes_to_agent_event_envelope(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            transcript = pathlib.Path(bindir) / "claude.raw.jsonl"
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_jsonl(bindir),
                transcript_path=str(transcript),
            )

            self.assertEqual(
                [event["event_type"] for event in res.normalized_events],
                ["thread.started", "message.completed", "command.completed"],
            )
            self.assertEqual(
                {event["schema"] for event in res.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            self.assertEqual(
                {event["provider"] for event in res.normalized_events},
                {"claude-code"},
            )
            self.assertTrue((pathlib.Path(bindir) / "events.normalized.jsonl").exists())

    def test_claude_tools_grant_filters_mcp_config_and_allowed_tools(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            source_config = pathlib.Path(config_dir) / "source-mcp.json"
            source_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "allowed": {"command": "/bin/echo", "args": ["allowed"]},
                            "forbidden": {
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
                transcript = pathlib.Path(bindir) / "claude.raw.jsonl"
                res = run_claude_lane(
                    sandbox=sandbox,
                    prompt="x",
                    claude_binary=_fake_claude_jsonl(bindir),
                    transcript_path=str(transcript),
                    tools={
                        "mcp": ["allowed"],
                        "allow": ["mcp__allowed__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                    lane_id="t-tools",
                )
            finally:
                if old_config is None:
                    os.environ.pop("WITNESSD_CLAUDE_MCP_CONFIG", None)
                else:
                    os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = old_config

            self.assertIn("--mcp-config", res.invocation)
            self.assertIn("--strict-mcp-config", res.invocation)
            self.assertIn("--allowedTools", res.invocation)
            allowed_tools_arg = res.invocation[
                res.invocation.index("--allowedTools") + 1
            ]
            self.assertIn("Edit", allowed_tools_arg)
            self.assertIn("Write", allowed_tools_arg)
            self.assertIn("Bash", allowed_tools_arg)
            self.assertNotIn("WebFetch", allowed_tools_arg)
            self.assertNotIn("WebSearch", allowed_tools_arg)
            self.assertIn("mcp__allowed__allowed_echo", allowed_tools_arg)
            self.assertIn("mcp__allowed__.*", allowed_tools_arg)
            generated = pathlib.Path(
                res.invocation[res.invocation.index("--mcp-config") + 1]
            )
            payload = json.loads(generated.read_text(encoding="utf-8"))
            self.assertEqual(list(payload["mcpServers"]), ["allowed"])
            self.assertEqual(res.tool_declaration["adapter"], "claude")

    def test_claude_execute_pep_denies_ungranted_network_builtin_and_receipts_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = pathlib.Path(temporary)
            denied = _execute_pep(task_dir, "WebFetch")

            self.assertEqual(denied.returncode, 0, denied.stderr)
            self.assertTrue(denied.stdout, "ungranted WebFetch must be denied")
            payload = json.loads(denied.stdout)
            self.assertEqual(
                payload["hookSpecificOutput"]["permissionDecision"], "deny"
            )
            allowed = _execute_pep(task_dir, "Edit")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, "")
            receipts = _build_claude_tool_decision_receipts(
                tools={"mcp": [], "allow": []},
                task_dir=task_dir,
                role_id="runner",
                role_capability="execute",
                lane_id="execute-1",
                observed_tool_uses=[],
            )
            self.assertEqual(
                [
                    (
                        item["canonical_tool_name"],
                        item["decision"],
                        item["reason_code"],
                    )
                    for item in receipts["all_tool_decisions"]
                ],
                [
                    (
                        "WebFetch",
                        "deny",
                        "ERR_ROLE_CAPABILITY_BUILTIN_TOOL_NOT_GRANTED",
                    ),
                    ("Edit", "allow", "ROLE_CAPABILITY_BUILTIN_TOOL_GRANTED"),
                ],
            )
            self.assertEqual(receipts["decisions"], [])
            self.assertIsNone(
                receipts["all_tool_decisions"][0]["previous_decision_sha256"]
            )
            expected_previous = hashlib.sha256(
                json.dumps(
                    receipts["all_tool_decisions"][0],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(
                receipts["all_tool_decisions"][1]["previous_decision_sha256"],
                expected_previous,
            )

    def test_claude_execute_pep_allows_default_file_and_build_builtins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = pathlib.Path(temporary)
            for tool_name in (
                "Read",
                "Glob",
                "Grep",
                "Edit",
                "Write",
                "NotebookEdit",
                "Bash",
            ):
                allowed = _execute_pep(task_dir, tool_name)
                self.assertEqual(allowed.returncode, 0, allowed.stderr)
                self.assertEqual(allowed.stdout, "", tool_name)

            policy = json.loads(
                (task_dir / "claude-tool-policy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                policy["builtin_allow"],
                ["Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit", "Bash"],
            )
            settings = json.loads(
                (task_dir / "claude-settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], ".*")
            decisions = [
                json.loads(line)
                for line in (task_dir / "tool-call-decisions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(
                {item["reason_code"] for item in decisions},
                {"ROLE_CAPABILITY_BUILTIN_TOOL_GRANTED"},
            )

    def test_claude_execute_pep_denies_unknown_builtin_with_empty_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            denied = _execute_pep(pathlib.Path(temporary), "CustomEscape")

            self.assertEqual(denied.returncode, 0, denied.stderr)
            self.assertTrue(denied.stdout, "unknown built-in must be denied")
            payload = json.loads(denied.stdout)
            self.assertIn(
                "ERR_ROLE_CAPABILITY_BUILTIN_TOOL_NOT_GRANTED",
                payload["hookSpecificOutput"]["permissionDecisionReason"],
            )

    def test_claude_execute_pep_allows_explicit_network_builtin_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = pathlib.Path(temporary)
            allowed = _execute_pep(task_dir, "WebSearch", allow=["WebSearch"])

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, "")
            policy = json.loads(
                (task_dir / "claude-tool-policy.json").read_text(encoding="utf-8")
            )
            self.assertIn("WebSearch", policy["builtin_allow"])
            decision = json.loads(
                (task_dir / "tool-call-decisions.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(
                decision["reason_code"], "ROLE_CAPABILITY_BUILTIN_TOOL_GRANTED"
            )

    def test_claude_tools_grant_installs_pretooluse_pep_advisory(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            source_config = pathlib.Path(config_dir) / "source-mcp.json"
            source_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "neutral_probe": {
                                "command": "/bin/echo",
                                "args": ["neutral"],
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
                transcript = pathlib.Path(bindir) / "claude.raw.jsonl"
                res = run_claude_lane(
                    sandbox=sandbox,
                    prompt="x",
                    claude_binary=_fake_claude_jsonl(bindir),
                    transcript_path=str(transcript),
                    tools={
                        "mcp": ["neutral_probe"],
                        "allow": ["mcp__neutral_probe__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                    lane_id="t-tools",
                )
            finally:
                if old_config is None:
                    os.environ.pop("WITNESSD_CLAUDE_MCP_CONFIG", None)
                else:
                    os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = old_config

            self.assertIn("--settings", res.invocation)
            self.assertIn("--include-hook-events", res.invocation)
            settings_path = pathlib.Path(
                res.invocation[res.invocation.index("--settings") + 1]
            )
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], ".*")
            hook_command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertTrue(
                hook_command.startswith("/usr/bin/python3 "),
                f"hook command must avoid python3 shim: {hook_command}",
            )
            self.assertEqual(
                res.tool_decision_advisory["kind"],
                "moonweave-tool-call-decision-advisory",
            )
            self.assertFalse(
                res.tool_decision_advisory["can_change_evidence_verdict"]
            )
            self.assertEqual(res.tool_decision_advisory["adapter"], "claude")
            self.assertEqual(
                res.tool_decision_advisory["policy"]["allow"],
                ["mcp__neutral_probe__allowed_echo"],
            )
            allow = subprocess.run(
                shlex.split(hook_command),
                input=json.dumps({"tool_name": "mcp__neutral_probe__allowed_echo"}),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(allow.returncode, 0, allow.stderr)
            self.assertEqual(allow.stdout, "")
            deny = subprocess.run(
                shlex.split(hook_command),
                input=json.dumps({"tool_name": "mcp__neutral_probe__neutral_check"}),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(deny.returncode, 0, deny.stderr)
            deny_payload = json.loads(deny.stdout)
            self.assertEqual(
                deny_payload["hookSpecificOutput"]["permissionDecision"], "deny"
            )
            builtin = subprocess.run(
                shlex.split(hook_command),
                input=json.dumps({"tool_name": "Read"}),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(builtin.returncode, 0, builtin.stderr)
            self.assertEqual(builtin.stdout, "")
            decisions = [
                json.loads(line)
                for line in pathlib.Path(
                    res.tool_decision_advisory["decision_log_path"]
                ).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [(item["canonical_tool_name"], item["decision"]) for item in decisions],
                [
                    ("mcp__neutral_probe__allowed_echo", "allow"),
                    ("mcp__neutral_probe__neutral_check", "deny"),
                    ("Read", "allow"),
                ],
            )
            self.assertEqual(
                decisions[-1]["reason_code"],
                "ROLE_CAPABILITY_BUILTIN_TOOL_GRANTED",
            )
            receipts = _build_claude_tool_decision_receipts(
                tools={
                    "mcp": ["neutral_probe"],
                    "allow": ["mcp__neutral_probe__allowed_echo"],
                },
                task_dir=pathlib.Path(res.transcript_path).parent,
                role_id="runner",
                role_capability="execute",
                lane_id="lane-1",
                observed_tool_uses=[
                    {
                        "tool_name": "mcp__neutral_probe__allowed_echo",
                        "tool_use_id": "tool-use-1",
                    }
                ],
            )
            self.assertEqual(
                receipts["kind"], "moonweave-tool-call-decision-receipts"
            )
            self.assertEqual(
                [item["canonical_tool_name"] for item in receipts["decisions"]],
                [
                    "mcp__neutral_probe__allowed_echo",
                    "mcp__neutral_probe__neutral_check",
                ],
            )
            self.assertEqual(
                [item["sequence"] for item in receipts["decisions"]],
                [1, 2],
            )
            self.assertEqual(
                [
                    item["canonical_tool_name"]
                    for item in receipts["all_tool_decisions"]
                ],
                [
                    "mcp__neutral_probe__allowed_echo",
                    "mcp__neutral_probe__neutral_check",
                    "Read",
                ],
            )
            self.assertEqual(
                receipts["observed_mcp_tool_calls"][0]["canonical_tool_name"],
                "mcp__neutral_probe__allowed_echo",
            )
            self.assertEqual(
                receipts["observed_mcp_tool_calls"][0]["result_status"],
                "observed",
            )
            self.assertEqual(
                receipts["observed_mcp_tool_calls"][0]["canonical_request_sha256"],
                receipts["decisions"][0]["canonical_request_sha256"],
            )

    def test_opencode(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_opencode_lane(
                sandbox=sandbox,
                prompt="x",
                opencode_binary=_fake_cli(bindir, "opencode"),
                transcript_path=str(pathlib.Path(bindir) / "opencode.txt"),
            )

            self._check(res, "opencode", sandbox)
            self.assertIn("run", res.invocation)

    def test_claude_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(ClaudeAdapterError) as cm:
                run_claude_lane(
                    sandbox=sandbox,
                    prompt="x",
                    claude_binary=_fake_cli(bindir, "claude"),
                    transcript_path=str(pathlib.Path(sandbox) / "claude.txt"),
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_opencode_transcript_path_inside_sandbox_rejected_failclosed(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            with self.assertRaises(OpenCodeAdapterError) as cm:
                run_opencode_lane(
                    sandbox=sandbox,
                    prompt="x",
                    opencode_binary=_fake_cli(bindir, "opencode"),
                    transcript_path=str(pathlib.Path(sandbox) / "opencode.txt"),
                )
            self.assertEqual(cm.exception.code, "ERR_EVIDENCE_NOT_SEPARATED")

    def test_model_passed_to_claude_argv(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(bindir),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="claude-sonnet-5",
            )

        self.assertIn("--model", res.invocation)
        self.assertEqual(
            res.invocation[res.invocation.index("--model") + 1], "claude-sonnet-5"
        )

    def test_no_model_requested_emits_no_declaration(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_cli(bindir, "claude"),
                transcript_path=str(pathlib.Path(bindir) / "claude.txt"),
            )

        self.assertNotIn("--model", res.invocation)
        self.assertIsNone(res.model_declaration)

    def test_valid_model_reports_verified(self):
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(
                    bindir, reject_model="bad-model"
                ),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="good-model",
            )

        self.assertEqual(res.exit_code, 0)
        self.assertEqual(
            res.model_declaration,
            {
                "kind": "moonweave-model-declaration",
                "schema_version": "1.0",
                "can_change_evidence_verdict": False,
                "adapter": "claude",
                "requested_model": "good-model",
                "verification_status": "verified",
                "detail": None,
            },
        )

    def test_invalid_model_rejected_by_claude_fails_closed(self):
        # Live-verified against real claude-code 2.1.207: an invalid --model
        # value does NOT change the process exit code (it stays 0) -- the
        # rejection only shows up as is_error/error on the terminal "result"
        # event. The lane must still fail closed rather than trusting exit 0.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            res = run_claude_lane(
                sandbox=sandbox,
                prompt="x",
                claude_binary=_fake_claude_model_probe(
                    bindir, reject_model="bad-model"
                ),
                transcript_path=str(pathlib.Path(bindir) / "claude.raw.jsonl"),
                model="bad-model",
            )

        self.assertEqual(res.exit_code, 125)
        self.assertEqual(res.test_output["status"], "failed")
        self.assertIn("bad-model", res.test_output["summary"])
        self.assertEqual(res.model_declaration["verification_status"], "rejected")
        self.assertEqual(res.model_declaration["requested_model"], "bad-model")
        self.assertIsNotNone(res.model_declaration["detail"])


if __name__ == "__main__":
    unittest.main()
