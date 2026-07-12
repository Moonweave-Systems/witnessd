"""Claude Code adapter for W4 runner lanes.

`--output-format stream-json` is required for claude to emit the structured
JSONL events normalize_claude_jsonl_events() parses -- without it, claude
only prints free text and there are no events to normalize. `--verbose` must
be passed alongside it: claude rejects `--print --output-format stream-json`
on its own with "Error: When using --print, --output-format=stream-json
requires --verbose" (live-verified against claude-code 2.1.207).
"""

from __future__ import annotations

import json
import hashlib
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from witnessd.adapters.base import (
    AdapterExecutionError,
    AdapterResult,
    RawRun,
    RunIntent,
    _resolve_executable,
    assert_evidence_path_separated,
)
from witnessd.adapters.shell import TEST_STATUS_NOT_RUN, _diff_touched, _snapshot
from witnessd.events import encode_agent_event_jsonl, normalize_claude_jsonl_events
from witnessd.model_declaration import (
    VERIFICATION_CONFIRMED,
    VERIFICATION_REJECTED,
    build_model_declaration,
)
from witnessd.tool_declaration import build_tool_declaration, normalize_tool_grant


class ClaudeAdapterError(AdapterExecutionError):
    pass


class ClaudeCLIAdapter:
    provider = "claude-code"

    def __init__(self, *, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary

    def compile_invocation(self, intent: RunIntent) -> list[str]:
        prompt = str(intent.get("prompt", "-"))
        model = intent.get("model")
        return [
            _claude_binary(self.claude_binary),
            "-p",
            prompt,
            *(["--model", str(model)] if model else []),
            "--output-format",
            "stream-json",
            "--verbose",
        ]

    def run(self, intent: RunIntent, sandbox: str) -> RawRun:
        invocation = self.compile_invocation(intent)
        completed = subprocess.run(
            invocation,
            cwd=sandbox,
            text=False,
            capture_output=True,
            check=False,
        )
        return RawRun(
            invocation=invocation,
            exit_code=completed.returncode,
            raw_events=completed.stdout or b"",
            stdout=(completed.stdout or b"").decode("utf-8", errors="replace"),
            stderr=(completed.stderr or b"").decode("utf-8", errors="replace"),
            effective_policy=self.effective_policy(
                RawRun(
                    invocation, completed.returncode, completed.stdout or b"", "", ""
                )
            ),
        )

    def normalize(self, raw: RawRun):
        return normalize_claude_jsonl_events(raw.raw_events)

    def effective_policy(self, raw: RawRun) -> dict[str, Any]:
        return {}


def _claude_binary(path: str) -> str:
    try:
        return _resolve_executable(path, unavailable_code="ERR_CLAUDE_UNAVAILABLE")
    except AdapterExecutionError as exc:
        raise ClaudeAdapterError(exc.code, exc.message) from exc


def _claude_model_rejection(raw_jsonl: bytes) -> str | None:
    """Scan for claude's own signal that the requested model was rejected
    (live-verified against claude-code 2.1.207 through the actual
    subprocess.run adapter path -- not just a manual terminal check, which
    first suggested a narrower shape than what actually happens): the
    process exit code is not a reliable signal by itself (observed both 0
    and 1 for the same rejection across separate runs), and the specific
    `error: "model_not_found"` code was found on the "assistant" message
    event, not the terminal "result" event as first assumed -- so this scans
    every event, not just "result". Narrow, exact match on that specific
    error code on purpose: claude's is_error can also fire for unrelated API
    errors, and only a model-rejection-specific signal should escalate this
    lane closed.
    """
    for line in raw_jsonl.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error") == "model_not_found":
            result_text = payload.get("result")
            if isinstance(result_text, str):
                return result_text
            message = payload.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        return item["text"]
            return "model_not_found"
    return None


def run_claude_lane(
    *,
    sandbox: str,
    prompt: str,
    claude_binary: str = "claude",
    transcript_path: str,
    log_path: str | None = None,
    timeout_seconds: int = 120,
    model: str | None = None,
    tools: dict[str, Any] | None = None,
    role_id: str | None = None,
    role_capability: str | None = None,
    lane_id: str | None = None,
) -> AdapterResult:
    if not prompt.strip():
        raise ClaudeAdapterError(
            "ERR_CLAUDE_PROMPT_MISSING", "claude prompt must not be empty"
        )
    repo = str(Path(sandbox).resolve(strict=False))
    transcript = str(Path(transcript_path).resolve(strict=False))
    task_dir = Path(transcript).parent
    normalized_transcript = str(Path(transcript).with_name("events.normalized.jsonl"))
    pep_paths = _claude_pep_paths(task_dir) if tools is not None else {}
    evidence_paths = [
        transcript,
        normalized_transcript,
        *([log_path] if log_path is not None else []),
        *[str(path) for path in pep_paths.values()],
    ]
    for evidence_path in evidence_paths:
        assert_evidence_path_separated(
            repo, evidence_path, error_cls=ClaudeAdapterError
        )
    Path(transcript).parent.mkdir(parents=True, exist_ok=True)
    invocation = [
        _claude_binary(claude_binary),
        "-p",
        prompt,
        *(["--model", model] if model else []),
        *_claude_tool_args(tools, task_dir),
        *_claude_pep_args(
            tools=tools,
            task_dir=task_dir,
            role_id=role_id or lane_id or "claude",
            role_capability=role_capability or "execute",
            lane_id=lane_id or role_id or "claude",
        ),
        "--output-format",
        "stream-json",
        "--verbose",
    ]

    before = _snapshot(repo)
    try:
        completed = subprocess.run(
            invocation,
            cwd=repo,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        raw_stdout = completed.stdout or b""
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        Path(transcript).write_bytes(raw_stdout)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        raw_stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else ""
        )
        Path(transcript).write_bytes(raw_stdout)
    except OSError as exc:
        exit_code = 127
        raw_stdout = b""
        stdout = ""
        stderr = str(exc)
        Path(transcript).write_bytes(raw_stdout)

    normalized_events = normalize_claude_jsonl_events(raw_stdout)
    Path(normalized_transcript).write_bytes(encode_agent_event_jsonl(normalized_events))
    test_output: dict[str, Any] = {"status": TEST_STATUS_NOT_RUN}
    model_declaration = None
    if model is not None:
        rejection = _claude_model_rejection(raw_stdout)
        if rejection is not None:
            exit_code = 125
            message = f"requested model {model} rejected: {rejection}"
            stderr = f"{stderr}\n{message}".strip()
            test_output = {"status": "failed", "summary": message}
            model_declaration = build_model_declaration(
                adapter="claude",
                requested_model=model,
                verification_status=VERIFICATION_REJECTED,
                detail=rejection,
            )
        else:
            model_declaration = build_model_declaration(
                adapter="claude",
                requested_model=model,
                verification_status=VERIFICATION_CONFIRMED,
            )
    tool_declaration = None
    tool_decision_advisory = None
    tool_decision_receipts = None
    if tools is not None:
        observed_tool_uses = _claude_observed_tool_uses(raw_stdout)
        tool_declaration = build_tool_declaration(
            role_id=role_id or lane_id or "claude",
            lane_id=lane_id or role_id or "claude",
            capability=role_capability or "execute",
            adapter="claude",
            declared_tools=normalize_tool_grant(tools),
            observed_tool_uses=observed_tool_uses,
            detail=None
            if observed_tool_uses
            else "claude stream-json did not include tool_use events for this run",
        )
        tool_decision_advisory = _build_claude_tool_decision_advisory(
            tools=tools,
            task_dir=task_dir,
            role_id=role_id or lane_id or "claude",
            role_capability=role_capability or "execute",
            lane_id=lane_id or role_id or "claude",
            raw_jsonl=raw_stdout,
            observed_tool_uses=observed_tool_uses,
        )
        tool_decision_receipts = _build_claude_tool_decision_receipts(
            tools=tools,
            task_dir=task_dir,
            role_id=role_id or lane_id or "claude",
            role_capability=role_capability or "execute",
            lane_id=lane_id or role_id or "claude",
            observed_tool_uses=observed_tool_uses,
        )

    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            json.dumps(
                {
                    "command": invocation,
                    "cwd": repo,
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    after = _snapshot(repo)
    return AdapterResult(
        adapter="claude",
        runner_kind="manual",
        invocation=invocation,
        exit_code=exit_code,
        transcript_path=transcript,
        command_receipts=[
            {
                "command": invocation,
                "cwd": repo,
                "exit_code": exit_code,
                "stdout": stdout[:4096],
                "stderr": stderr[:4096],
            }
        ],
        touched_files=_diff_touched(
            before, after, sandbox=repo, evidence_paths=evidence_paths
        ),
        test_output=test_output,
        normalized_events=normalized_events,
        raw_events_path=transcript,
        normalized_events_path=normalized_transcript,
        model_declaration=model_declaration,
        tool_declaration=tool_declaration,
        tool_decision_advisory=tool_decision_advisory,
        tool_decision_receipts=tool_decision_receipts,
    )


def _claude_tool_args(tools: dict[str, Any] | None, task_dir: Path) -> list[str]:
    if tools is None:
        return []
    normalized = normalize_tool_grant(tools)
    config_path = task_dir / "claude-mcp-config.json"
    config_path.write_text(
        json.dumps(_filtered_claude_mcp_config(normalized["mcp"]), sort_keys=True),
        encoding="utf-8",
    )
    args = ["--mcp-config", str(config_path), "--strict-mcp-config"]
    allowed_tools = _claude_allowed_tools_arg(normalized)
    if allowed_tools:
        args.extend(["--allowedTools", ",".join(allowed_tools)])
    else:
        args.extend(["--allowedTools", ""])
    return args


def _claude_allowed_tools_arg(normalized: dict[str, list[str]]) -> list[str]:
    values: list[str] = []
    for name in normalized["allow"]:
        if name not in values:
            values.append(name)
    for server_id in normalized["mcp"]:
        pattern = f"mcp__{server_id}__.*"
        if pattern not in values:
            values.append(pattern)
    return values


def _claude_pep_paths(task_dir: Path) -> dict[str, Path]:
    return {
        "settings": task_dir / "claude-settings.json",
        "policy": task_dir / "claude-tool-policy.json",
        "hook": task_dir / "claude-pretooluse-pep.py",
        "decisions": task_dir / "tool-call-decisions.jsonl",
    }


def _claude_pep_args(
    *,
    tools: dict[str, Any] | None,
    task_dir: Path,
    role_id: str,
    role_capability: str,
    lane_id: str,
) -> list[str]:
    if tools is None:
        return []
    normalized = normalize_tool_grant(tools)
    paths = _claude_pep_paths(task_dir)
    policy = {
        "kind": "moonweave-claude-pretooluse-policy",
        "schema_version": "1.0",
        "adapter": "claude",
        "role_id": role_id,
        "lane_id": lane_id,
        "capability": role_capability,
        "mcp": normalized["mcp"],
        "allow": normalized["allow"],
        "deny_by_default": True,
        "match": "exact",
    }
    paths["policy"].write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths["decisions"].write_text("", encoding="utf-8")
    paths["hook"].write_text(_CLAUDE_PRETOOLUSE_HOOK, encoding="utf-8")
    paths["hook"].chmod(0o700)
    command = " ".join(
        [
            shlex.quote("/usr/bin/python3"),
            shlex.quote(str(paths["hook"])),
            shlex.quote(str(paths["policy"])),
            shlex.quote(str(paths["decisions"])),
        ]
    )
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__.*",
                    "hooks": [{"type": "command", "command": command}],
                }
            ]
        }
    }
    paths["settings"].write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return ["--settings", str(paths["settings"]), "--include-hook-events"]


_CLAUDE_PRETOOLUSE_HOOK = r'''from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


def _load_json(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tool_name(payload: dict) -> str:
    for key in ("tool_name", "toolName", "name"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    tool = payload.get("tool")
    if isinstance(tool, dict):
        value = tool.get("name")
        if isinstance(value, str):
            return value
    return ""


def _next_sequence(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) + 1
    except OSError:
        return 1


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    policy = _load_json(sys.argv[1])
    decisions_path = Path(sys.argv[2])
    raw_stdin = sys.stdin.read()
    try:
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    name = _tool_name(payload)
    if not name.startswith("mcp__"):
        allowed = True
        reason = "CLAUDE_BUILTIN_TOOL_OUT_OF_SCOPE"
    else:
        allowed = name in set(policy.get("allow", []))
        reason = "ROLE_CAPABILITY_TOOL_GRANTED" if allowed else "ERR_ROLE_CAPABILITY_TOOL_NOT_GRANTED"
    decision = "allow" if allowed else "deny"
    sequence = _next_sequence(decisions_path)
    record = {
        "kind": "moonweave-tool-call-decision",
        "schema_version": "1.0",
        "can_change_evidence_verdict": False,
        "adapter": "claude",
        "role_id": policy.get("role_id"),
        "lane_id": policy.get("lane_id"),
        "capability": policy.get("capability"),
        "sequence": sequence,
        "canonical_tool_name": name,
        "decision": decision,
        "reason_code": reason,
        "observed_at_unix_ms": int(time.time() * 1000),
        "request_sha256": hashlib.sha256(raw_stdin.encode("utf-8")).hexdigest(),
    }
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    with decisions_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")

    if allowed:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "R4_PEP_DENY ERR_ROLE_CAPABILITY_TOOL_NOT_GRANTED",
        }
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _build_claude_tool_decision_advisory(
    *,
    tools: dict[str, Any],
    task_dir: Path,
    role_id: str,
    role_capability: str,
    lane_id: str,
    raw_jsonl: bytes,
    observed_tool_uses: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_tool_grant(tools)
    paths = _claude_pep_paths(task_dir)
    decisions = _read_jsonl_objects(paths["decisions"])
    return {
        "kind": "moonweave-tool-call-decision-advisory",
        "schema_version": "1.0",
        "can_change_evidence_verdict": False,
        "adapter": "claude",
        "role_id": role_id,
        "lane_id": lane_id,
        "capability": role_capability,
        "policy": {
            "mcp": normalized["mcp"],
            "allow": normalized["allow"],
            "deny_by_default": True,
            "match": "exact",
        },
        "decisions": decisions,
        "decision_log_path": str(paths["decisions"]),
        "settings_path": str(paths["settings"]),
        "hook_script_path": str(paths["hook"]),
        "stream_reconciliation": {
            "include_hook_events": True,
            "observed_tool_uses": observed_tool_uses,
            "hook_event_count": _claude_hook_event_count(raw_jsonl),
            "source_of_decision": "pretooluse-hook-log",
        },
        "detail": "witnessd-local advisory only; Depone does not re-derive this artifact",
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_decision_hash(decision: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(decision).encode("utf-8")).hexdigest()


def _observed_request_sha256(
    observed: dict[str, Any],
    decisions_by_name: dict[str, list[dict[str, Any]]],
) -> str:
    tool_name = observed.get("tool_name")
    if isinstance(tool_name, str):
        candidates = decisions_by_name.get(tool_name, [])
        for candidate in candidates:
            if candidate.get("decision") == "allow":
                request_sha = candidate.get("canonical_request_sha256")
                if isinstance(request_sha, str):
                    return request_sha
        if candidates:
            request_sha = candidates[0].get("canonical_request_sha256")
            if isinstance(request_sha, str):
                return request_sha
    return hashlib.sha256(_canonical_json(observed).encode("utf-8")).hexdigest()


def _build_claude_tool_decision_receipts(
    *,
    tools: dict[str, Any],
    task_dir: Path,
    role_id: str,
    role_capability: str,
    lane_id: str,
    observed_tool_uses: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_tool_grant(tools)
    paths = _claude_pep_paths(task_dir)
    source_decisions = _read_jsonl_objects(paths["decisions"])
    decisions: list[dict[str, Any]] = []
    previous_hash: str | None = None
    decisions_by_name: dict[str, list[dict[str, Any]]] = {}
    for source in source_decisions:
        canonical_tool_name = source.get("canonical_tool_name")
        if not isinstance(canonical_tool_name, str) or not canonical_tool_name.startswith(
            "mcp__"
        ):
            continue
        request_sha = source.get("canonical_request_sha256")
        if not isinstance(request_sha, str):
            request_sha = source.get("request_sha256")
        if not isinstance(request_sha, str):
            request_sha = hashlib.sha256(_canonical_json(source).encode("utf-8")).hexdigest()
        receipt = {
            "sequence": len(decisions) + 1,
            "source_sequence": source.get("sequence"),
            "canonical_tool_name": canonical_tool_name,
            "canonical_request_sha256": request_sha,
            "decision": source.get("decision"),
            "reason_code": source.get("reason_code"),
            "previous_decision_sha256": previous_hash,
        }
        decisions.append(receipt)
        decisions_by_name.setdefault(canonical_tool_name, []).append(receipt)
        previous_hash = _canonical_decision_hash(receipt)

    observed_mcp_tool_calls: list[dict[str, Any]] = []
    for observed in observed_tool_uses:
        tool_name = observed.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
            continue
        observed_mcp_tool_calls.append(
            {
                "canonical_tool_name": tool_name,
                "canonical_request_sha256": _observed_request_sha256(
                    observed,
                    decisions_by_name,
                ),
                "tool_use_id": observed.get("tool_use_id"),
                "result_status": "observed",
            }
        )

    return {
        "kind": "moonweave-tool-call-decision-receipts",
        "schema_version": "1.0",
        "adapter": "claude",
        "role_id": role_id,
        "lane_id": lane_id,
        "capability": role_capability,
        "decisions": decisions,
        "observed_mcp_tool_calls": observed_mcp_tool_calls,
        "policy_ref": {
            "mcp": normalized["mcp"],
            "allow": normalized["allow"],
            "deny_by_default": True,
            "match": "exact",
        },
        "boundary": {
            "can_change_evidence_verdict": True,
            "non_mcp_tools_out_of_scope": True,
            "source_of_decision": "pretooluse-hook-log",
        },
    }


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _claude_hook_event_count(raw_jsonl: bytes) -> int:
    count = 0
    for line in raw_jsonl.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if _contains_hook_event(payload):
            count += 1
    return count


def _contains_hook_event(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and "hook" in key.lower():
                return True
            if isinstance(item, str) and "hook" in item.lower():
                return True
            if _contains_hook_event(item):
                return True
    elif isinstance(value, list):
        return any(_contains_hook_event(item) for item in value)
    return False


def _filtered_claude_mcp_config(allowed_mcp: list[str]) -> dict[str, Any]:
    source = os.environ.get("WITNESSD_CLAUDE_MCP_CONFIG")
    if not source:
        return {"mcpServers": {}}
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mcpServers": {}}
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        return {"mcpServers": {}}
    return {
        "mcpServers": {
            name: servers[name]
            for name in allowed_mcp
            if name in servers and isinstance(servers[name], dict)
        }
    }


def _claude_observed_tool_uses(raw_jsonl: bytes) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for line in raw_jsonl.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        _collect_tool_uses(payload, observed)
    return observed


def _collect_tool_uses(value: Any, observed: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if value.get("type") == "tool_use":
            name = value.get("name")
            observed.append(
                {
                    "tool_name": name if isinstance(name, str) else None,
                    "tool_use_id": value.get("id") if isinstance(value.get("id"), str) else None,
                }
            )
        for item in value.values():
            _collect_tool_uses(item, observed)
    elif isinstance(value, list):
        for item in value:
            _collect_tool_uses(item, observed)
