#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "orro-engine-contract-v0.md"
CONFORMANCE = ROOT / "docs" / "orro-conformance" / "README.md"

REQUIRED_ARTIFACTS = [
    "repo-profile.json",
    "context-pack.json",
    "sealed-plan.json",
    "workflow-plan.json",
    "workflow-plan-binding.json",
    "role-lane-plan.json",
    "role-lane-plan-binding.json",
    "workflow-role-dispatch.json",
    "team-ledger.json",
    "team-ledger-verdict.json",
    "verification-recipe.json",
    "verification-receipt.json",
    "proofcheck-verdict.json",
    "orro-continuation-decision.json",
    "orro-auto-plan.json",
    "orro-auto-receipt.json",
    "orro-auto-session.json",
    "orro-report.json",
    "orro-handoff.json",
    "orro-engine-lock.json",
]

REQUIRED_CONTRACT_TEXT = [
    "Depone verifies; witnessd executes; ORRO exposes the workflow.",
    "Depone remains",
    "verifier-authoritative",
    "Workflow plan is intent, not proof.",
    "Role-lane plan is executable intent, not proof.",
    "Role dispatch is context, not proof.",
    "Auto artifacts are orchestration metadata, not proof.",
    "Report is summary, not proof.",
    "Handoff is review package, not approval.",
    "Engine-lock is distribution metadata, not proof.",
    "Existing proofcheck verdict is not an input trust root.",
    "Verification recipe is intent.",
    "Verification receipt is execution evidence only if valid.",
    "MCP/tool output is observed fact, not trust root.",
    "Scout-only directories must not proofcheck-pass.",
    "Proofrun evidence must exist before proofcheck can pass.",
    "Handoff requires a passing bound `proofcheck-verdict.json`.",
    "Auto may not bypass proofcheck or handoff gates.",
    "Report may not upgrade status beyond observed artifacts.",
    "Become a third engine",
    "Superflow",
    "historical compatibility only",
]

REQUIRED_CONFORMANCE_TEXT = [
    "Depone is verifier-authoritative",
    "docs/orro-conformance/manifest.json",
    "valid-team-ledger-run",
    "scout-only",
    "workflow-plan-only",
    "wrapper-artifacts-only",
    "stale-proofcheck-verdict",
    "Depone verifies; witnessd executes; ORRO exposes the workflow.",
]


def _read(path: Path, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing file: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")


def check() -> list[str]:
    errors: list[str] = []
    contract = _read(CONTRACT, errors)
    conformance = _read(CONFORMANCE, errors)

    for artifact in REQUIRED_ARTIFACTS:
        if artifact not in contract:
            errors.append(f"contract missing artifact: {artifact}")

    for text in REQUIRED_CONTRACT_TEXT:
        if text not in contract:
            errors.append(f"contract missing required text: {text}")

    for text in REQUIRED_CONFORMANCE_TEXT:
        if text not in conformance:
            errors.append(f"conformance README missing required text: {text}")

    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"check_orro_engine_contract: {error}", file=sys.stderr)
        return 1
    print("check_orro_engine_contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
