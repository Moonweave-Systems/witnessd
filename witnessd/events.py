"""Provider event normalization for Evidence v2."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, TypedDict

from witnessd.canonical import canonical_hash

AGENT_EVENT_SCHEMA_V1 = "moonweave.agent-event/v1"


class AgentEventEnvelope(TypedDict):
    schema: str
    seq: int
    wall_time: str
    monotonic_ns: int
    provider: str
    provider_version: str | None
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    event_type: str
    raw_event_sha256: str
    payload_sha256: str
    prev_event_hash: str | None
    redaction_manifest_ref: str | None


def normalize_codex_jsonl_events(
    raw_jsonl: bytes,
    *,
    provider_version: str | None = None,
) -> list[AgentEventEnvelope]:
    envelopes: list[AgentEventEnvelope] = []
    prev_event_hash: str | None = None
    for seq, line in enumerate(_jsonl_lines(raw_jsonl)):
        payload = _parse_json_line(line)
        envelope: AgentEventEnvelope = {
            "schema": AGENT_EVENT_SCHEMA_V1,
            "seq": seq,
            "wall_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "monotonic_ns": time.monotonic_ns(),
            "provider": "codex-cli",
            "provider_version": provider_version,
            "thread_id": _string_or_none(payload.get("thread_id")),
            "turn_id": _string_or_none(payload.get("turn_id")),
            "item_id": _item_id(payload),
            "event_type": _event_type(payload),
            "raw_event_sha256": hashlib.sha256(line).hexdigest(),
            "payload_sha256": _payload_hash(payload),
            "prev_event_hash": prev_event_hash,
            "redaction_manifest_ref": None,
        }
        prev_event_hash = canonical_hash(envelope)
        envelopes.append(envelope)
    return envelopes


def normalize_claude_jsonl_events(
    raw_jsonl: bytes,
    *,
    provider_version: str | None = None,
) -> list[AgentEventEnvelope]:
    envelopes: list[AgentEventEnvelope] = []
    prev_event_hash: str | None = None
    for seq, line in enumerate(_jsonl_lines(raw_jsonl)):
        payload = _parse_json_line(line)
        envelope: AgentEventEnvelope = {
            "schema": AGENT_EVENT_SCHEMA_V1,
            "seq": seq,
            "wall_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "monotonic_ns": time.monotonic_ns(),
            "provider": "claude-code",
            "provider_version": provider_version,
            "thread_id": _string_or_none(payload.get("session_id")),
            "turn_id": _string_or_none(payload.get("turn_id")),
            "item_id": _string_or_none(payload.get("message_id"))
            or _string_or_none(payload.get("tool_use_id"))
            or _item_id(payload),
            "event_type": _claude_event_type(payload),
            "raw_event_sha256": hashlib.sha256(line).hexdigest(),
            "payload_sha256": _payload_hash(payload),
            "prev_event_hash": prev_event_hash,
            "redaction_manifest_ref": None,
        }
        prev_event_hash = canonical_hash(envelope)
        envelopes.append(envelope)
    return envelopes


def normalize_gemini_jsonl_events(
    raw_jsonl: bytes,
    *,
    provider_version: str | None = None,
) -> list[AgentEventEnvelope]:
    envelopes: list[AgentEventEnvelope] = []
    prev_event_hash: str | None = None
    for seq, line in enumerate(_jsonl_lines(raw_jsonl)):
        payload = _parse_json_line(line)
        envelope: AgentEventEnvelope = {
            "schema": AGENT_EVENT_SCHEMA_V1,
            "seq": seq,
            "wall_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "monotonic_ns": time.monotonic_ns(),
            "provider": "google-gemini",
            "provider_version": provider_version,
            "thread_id": _string_or_none(payload.get("session_id"))
            or _string_or_none(payload.get("conversation_id")),
            "turn_id": _string_or_none(payload.get("turn_id")),
            "item_id": _string_or_none(payload.get("id"))
            or _string_or_none(payload.get("message_id"))
            or _item_id(payload),
            "event_type": _gemini_event_type(payload),
            "raw_event_sha256": hashlib.sha256(line).hexdigest(),
            "payload_sha256": _payload_hash(payload),
            "prev_event_hash": prev_event_hash,
            "redaction_manifest_ref": None,
        }
        prev_event_hash = canonical_hash(envelope)
        envelopes.append(envelope)
    return envelopes


def normalize_agy_text_events(
    raw_text: bytes,
    *,
    provider_version: str | None = None,
) -> list[AgentEventEnvelope]:
    if not raw_text.strip():
        return []
    decoded = raw_text.decode("utf-8", errors="replace")
    payload = {"type": "agent_message.final", "text": decoded}
    return [
        {
            "schema": AGENT_EVENT_SCHEMA_V1,
            "seq": 0,
            "wall_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "monotonic_ns": time.monotonic_ns(),
            "provider": "google-antigravity",
            "provider_version": provider_version,
            "thread_id": None,
            "turn_id": None,
            "item_id": None,
            "event_type": "message.completed",
            "raw_event_sha256": hashlib.sha256(raw_text).hexdigest(),
            "payload_sha256": _payload_hash(payload),
            "prev_event_hash": None,
            "redaction_manifest_ref": None,
        }
    ]


def normalize_agy_jsonl_events(
    raw_jsonl: bytes,
    *,
    provider_version: str | None = None,
) -> list[AgentEventEnvelope]:
    return normalize_agy_text_events(raw_jsonl, provider_version=provider_version)


def encode_agent_event_jsonl(events: list[AgentEventEnvelope]) -> bytes:
    return b"".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for event in events
    )


def _jsonl_lines(raw_jsonl: bytes) -> list[bytes]:
    return [line for line in raw_jsonl.splitlines() if line.strip()]


def _parse_json_line(line: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"type": None, "raw_unparsed": line.decode("utf-8", errors="replace")}
    return payload if isinstance(payload, dict) else {"type": None, "payload": payload}


def _event_type(payload: dict[str, Any]) -> str:
    source_type = payload.get("type")
    if source_type == "thread.started":
        return "thread.started"
    if source_type == "item.completed":
        item = payload.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "command_execution":
                return "command.completed"
            if item_type == "message":
                return "message.completed"
    return "provider.unknown"


def _claude_event_type(payload: dict[str, Any]) -> str:
    source_type = payload.get("type")
    if source_type == "session.started":
        return "thread.started"
    if source_type in {"assistant.message", "assistant"}:
        return "message.completed"
    if source_type in {"tool.completed", "post_tool_use"}:
        return "command.completed"
    if source_type in {"result", "result.message"}:
        return "turn.completed"
    return "provider.unknown"


def _gemini_event_type(payload: dict[str, Any]) -> str:
    source_type = payload.get("type")
    if source_type in {"message", "assistant", "content", "model.output"}:
        return "message.completed"
    if source_type in {"tool_call", "tool.completed", "tool_result", "function_call"}:
        return "command.completed"
    if source_type in {"result", "final", "response"}:
        return "turn.completed"
    return "provider.unknown"


def _item_id(payload: dict[str, Any]) -> str | None:
    item = payload.get("item")
    if isinstance(item, dict):
        return _string_or_none(item.get("id"))
    return _string_or_none(payload.get("item_id"))


def _payload_hash(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
