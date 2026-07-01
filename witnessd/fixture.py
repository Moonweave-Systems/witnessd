"""Build the Depone-valid source fixture a lane's capture-manifest embeds.

The source fixture is the *declared* (A0, non-authoritative) side of a capture;
the observer capture is the *observed* (A1) side. Depone's
``validate_capture_manifest`` requires the source fixture to be an
``agent-fabric-reference-adapter-fixture`` (schema 1.0, with ``invocation`` and
``capture`` blocks and an object ``adapter``).

witnessd is stdlib-only and MUST NOT import depone at runtime, so this replicates
depone's reference-adapter-fixture contract. If it drifts from depone, the
conformance tests (``tests/test_cli.py::TestRunDeponeValid`` and the emitter /
capture-manifest tests) fail against the real depone validator — that failure is
the intended drift guard.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REFERENCE_ADAPTER_FIXTURE_VERSION = "1.0"
FIXTURE_KIND = "agent-fabric-reference-adapter-fixture"
FIXTURE_MODE = "fixture-only"
FIXTURE_TRUST_LEVEL = "A0-claims-only"


def build_shell_invocation(profile: str) -> dict[str, Any]:
    """A declared shell-runner invocation packet for a lane."""

    return {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": profile,
        "role": "runner",
        "toolbelt": {
            "allowed_tools": ["cat", "python3"],
            "allowed_mcp": [],
            "forbidden_tools": ["write"],
            "context_policy": "local-code-only",
            "output_schema": "runner-result-v1",
            "evidence_obligations": ["command_receipt"],
        },
        "instructions": "Run checks and report outputs.",
        "evidence_obligations": ["command_receipt"],
        "context_policy": "local-code-only",
    }


def _default_result(invocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_version": "1.0",
        "agent_role": str(invocation.get("role", "unknown")),
        "profile": str(invocation.get("profile", "unknown")),
        "status": "partial",
        "output_files": [],
        "self_reported_claims": [],
        "command_receipts": [],
        "errors": ["fixture-only adapter did not execute work"],
    }


def _default_diff_summary() -> dict[str, Any]:
    return {
        "changed_files": [],
        "added_files": [],
        "modified_files": [],
        "deleted_files": [],
        "summary": "fixture-only adapter did not observe a diff",
    }


def _default_test_output() -> dict[str, Any]:
    return {
        "status": "not-run",
        "command": None,
        "summary": "fixture-only adapter did not run tests",
    }


def build_reference_adapter_fixture(invocation: dict[str, Any]) -> dict[str, Any]:
    """A deterministic, non-executing shell reference-adapter fixture matching
    depone's ``agent-fabric-reference-adapter-fixture`` contract."""

    harness = str(invocation.get("target_harness", "unknown"))
    return {
        "schema_version": REFERENCE_ADAPTER_FIXTURE_VERSION,
        "kind": FIXTURE_KIND,
        "adapter": {
            "name": "shell-reference-fixture",
            "harness": harness,
            "mode": FIXTURE_MODE,
            "executes_commands": False,
        },
        "invocation": deepcopy(invocation),
        "capture": {
            "trust_level": FIXTURE_TRUST_LEVEL,
            "self_report": _default_result(invocation),
            "diff_summary": _default_diff_summary(),
            "touched_files": [],
            "test_output": _default_test_output(),
            "command_receipts": [],
        },
    }
