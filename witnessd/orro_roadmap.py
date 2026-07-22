"""ORRO roadmap ledger and declared run-binding helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROADMAP_KIND = "orro-roadmap"
ROADMAP_SCHEMA_VERSION = "0.1"
ROADMAP_BINDING_KIND = "orro-roadmap-binding"
ROADMAP_BINDING_SCHEMA_VERSION = "0.1"

ERR_ORRO_ROADMAP_INVALID = "ERR_ORRO_ROADMAP_INVALID"
ERR_ORRO_ROADMAP_ITEM_UNKNOWN = "ERR_ORRO_ROADMAP_ITEM_UNKNOWN"
ERR_ORRO_ROADMAP_STEP_UNKNOWN = "ERR_ORRO_ROADMAP_STEP_UNKNOWN"
ERR_ORRO_ROADMAP_STEP_REQUIRES_ITEM = "ERR_ORRO_ROADMAP_STEP_REQUIRES_ITEM"
ERR_ORRO_ROADMAP_WRITE_FAILED = "ERR_ORRO_ROADMAP_WRITE_FAILED"

_ITEM_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ROADMAP_KEYS = {"kind", "schema_version", "items"}
_ITEM_KEYS = {"id", "title", "status", "note", "spec", "steps"}
_STEP_KEYS = {"id", "title", "profile", "write_scope", "checks", "commands", "adapter"}
_PROFILES = {
    "code-change", "review-only", "verification-only", "docs-change", "release-readiness"
}
_BINDING_KEYS = {
    "kind",
    "schema_version",
    "item_id",
    "ledger_path",
    "ledger_sha256",
    "step_id",
}


class OrroRoadmapError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def roadmap_path(repo: Path) -> Path:
    return repo / ".orro" / "roadmap.json"


def read_roadmap(repo: Path) -> dict[str, Any] | None:
    path = roadmap_path(repo)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrroRoadmapError(
            ERR_ORRO_ROADMAP_INVALID, f"cannot read .orro/roadmap.json: {exc}"
        ) from exc
    return _validate_roadmap(payload)


def write_roadmap(repo: Path, roadmap: dict[str, Any]) -> Path:
    payload = _validate_roadmap(roadmap)
    path = roadmap_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise OrroRoadmapError(ERR_ORRO_ROADMAP_WRITE_FAILED, str(exc)) from exc
    return path


def read_roadmap_binding(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "roadmap-binding.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrroRoadmapError(
            ERR_ORRO_ROADMAP_INVALID, f"cannot read roadmap-binding.json: {exc}"
        ) from exc
    return _validate_binding(payload)


def seal_roadmap_binding(
    *, repo: Path, run_dir: Path, item_id: str, step_id: str | None = None
) -> dict[str, Any]:
    item = require_roadmap_item(repo, item_id)
    if step_id is not None:
        require_roadmap_step(repo, item_id, step_id, item=item)

    ledger = roadmap_path(repo)
    binding = {
        "kind": ROADMAP_BINDING_KIND,
        "schema_version": ROADMAP_BINDING_SCHEMA_VERSION,
        "item_id": item_id,
        "ledger_path": ".orro/roadmap.json",
        "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
    }
    if step_id is not None:
        binding["step_id"] = step_id
    path = run_dir / "roadmap-binding.json"
    try:
        path.write_text(
            json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise OrroRoadmapError(ERR_ORRO_ROADMAP_WRITE_FAILED, str(exc)) from exc
    readable = read_roadmap_binding(run_dir)
    if readable is None:
        raise OrroRoadmapError(
            ERR_ORRO_ROADMAP_WRITE_FAILED, "roadmap binding was not readable"
        )
    return readable


def require_roadmap_item(repo: Path, item_id: str) -> dict[str, Any]:
    roadmap = read_roadmap(repo)
    items = roadmap["items"] if roadmap is not None else []
    for item in items:
        if item["id"] == item_id:
            return item
    raise OrroRoadmapError(
        ERR_ORRO_ROADMAP_ITEM_UNKNOWN,
        "roadmap item is not present in .orro/roadmap.json: "
        f"{item_id}; known roadmap item ids: "
        + ", ".join(str(item["id"]) for item in items)
        if items
        else f"{item_id}; known roadmap item ids: (none)",
    )


def require_roadmap_step(
    repo: Path, item_id: str | None, step_id: str, *, item: dict[str, Any] | None = None
) -> dict[str, Any]:
    if item_id is None:
        raise OrroRoadmapError(
            ERR_ORRO_ROADMAP_STEP_REQUIRES_ITEM,
            "--roadmap-step requires --roadmap-item",
        )
    item = item or require_roadmap_item(repo, item_id)
    steps = item.get("steps", [])
    for step in steps:
        if step["id"] == step_id:
            return step
    raise OrroRoadmapError(
        ERR_ORRO_ROADMAP_STEP_UNKNOWN,
        f"roadmap step is not present on item {item_id}: {step_id}; known roadmap step ids: "
        + ", ".join(str(step["id"]) for step in steps)
        if steps
        else f"{step_id}; known roadmap step ids: (none)",
    )


def _validate_roadmap(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _invalid(".orro/roadmap.json must contain an object")
    if set(payload) != _ROADMAP_KEYS:
        _invalid(".orro/roadmap.json must contain kind, schema_version, and items")
    if payload.get("kind") != ROADMAP_KIND:
        _invalid(f".orro/roadmap.json kind must be {ROADMAP_KIND}")
    if payload.get("schema_version") != ROADMAP_SCHEMA_VERSION:
        _invalid(f".orro/roadmap.json schema_version must be {ROADMAP_SCHEMA_VERSION}")
    items = payload.get("items")
    if not isinstance(items, list):
        _invalid(".orro/roadmap.json items must be a list")

    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            _invalid(f".orro/roadmap.json item {index} must be an object")
        if not set(item).issubset(_ITEM_KEYS) or not {"id", "title"}.issubset(item):
            _invalid(f".orro/roadmap.json item {index} has invalid fields")
        item_id = item.get("id")
        if not isinstance(item_id, str) or _ITEM_ID.fullmatch(item_id) is None:
            _invalid(f".orro/roadmap.json item {index}.id must be kebab-case")
        if item_id in seen:
            _invalid(f".orro/roadmap.json item id is duplicated: {item_id}")
        seen.add(item_id)
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            _invalid(f".orro/roadmap.json item {index}.title must be a non-empty string")
        if "status" in item and item.get("status") != "done":
            _invalid(f".orro/roadmap.json item {index}.status must be done")
        if "status" in item and "steps" in item:
            _invalid(f".orro/roadmap.json item {index} may not have both status and steps")
        for key in ("note", "spec"):
            if key in item and not isinstance(item.get(key), str):
                _invalid(f".orro/roadmap.json item {index}.{key} must be a string")
        if "steps" in item:
            steps = item["steps"]
            if not isinstance(steps, list):
                _invalid(f".orro/roadmap.json item {index}.steps must be a list")
            step_ids: set[str] = set()
            for step_index, step in enumerate(steps):
                _validate_step(step, index=index, step_index=step_index, seen=step_ids)
    return payload


def _validate_binding(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) not in (
        _BINDING_KEYS - {"step_id"}, _BINDING_KEYS
    ):
        _invalid("roadmap-binding.json has invalid fields")
    if payload.get("kind") != ROADMAP_BINDING_KIND:
        _invalid(f"roadmap-binding.json kind must be {ROADMAP_BINDING_KIND}")
    if payload.get("schema_version") != ROADMAP_BINDING_SCHEMA_VERSION:
        _invalid(
            f"roadmap-binding.json schema_version must be {ROADMAP_BINDING_SCHEMA_VERSION}"
        )
    item_id = payload.get("item_id")
    if not isinstance(item_id, str) or _ITEM_ID.fullmatch(item_id) is None:
        _invalid("roadmap-binding.json item_id must be kebab-case")
    if payload.get("ledger_path") != ".orro/roadmap.json":
        _invalid("roadmap-binding.json ledger_path must be .orro/roadmap.json")
    digest = payload.get("ledger_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        _invalid("roadmap-binding.json ledger_sha256 must be lowercase SHA-256")
    if "step_id" in payload and (
        not isinstance(payload["step_id"], str) or _ITEM_ID.fullmatch(payload["step_id"]) is None
    ):
        _invalid("roadmap-binding.json step_id must be kebab-case")
    return payload


def _validate_step(
    step: Any, *, index: int, step_index: int, seen: set[str]
) -> None:
    if not isinstance(step, dict) or not set(step).issubset(_STEP_KEYS) or not {"id", "profile"}.issubset(step):
        _invalid(f".orro/roadmap.json item {index}.step {step_index} has invalid fields")
    step_id = step.get("id")
    if not isinstance(step_id, str) or _ITEM_ID.fullmatch(step_id) is None:
        _invalid(f".orro/roadmap.json item {index}.step {step_index}.id must be kebab-case")
    if step_id in seen:
        _invalid(f".orro/roadmap.json step id is duplicated: {step_id}")
    seen.add(step_id)
    if not isinstance(step.get("profile"), str) or step.get("profile") not in _PROFILES:
        _invalid(f".orro/roadmap.json item {index}.step {step_index}.profile is invalid")
    if "title" in step and (not isinstance(step["title"], str) or not step["title"].strip()):
        _invalid(f".orro/roadmap.json item {index}.step {step_index}.title must be a non-empty string")
    for key in ("write_scope", "checks", "commands"):
        if key in step and (
            not isinstance(step[key], list) or any(not isinstance(value, str) for value in step[key])
        ):
            _invalid(f".orro/roadmap.json item {index}.step {step_index}.{key} must be a list of strings")
    if "adapter" in step and not isinstance(step["adapter"], str):
        _invalid(f".orro/roadmap.json item {index}.step {step_index}.adapter must be a string")


def _invalid(message: str) -> None:
    raise OrroRoadmapError(ERR_ORRO_ROADMAP_INVALID, message)
