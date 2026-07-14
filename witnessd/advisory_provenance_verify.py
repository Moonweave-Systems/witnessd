"""Offline Depone-backed verification for sealed ORRO advisory provenance."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from witnessd.advisory_provenance import ADVISORY_PROVENANCE_SCHEMA_VERSION


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            json.dumps(
                {
                    "decision": "BLOCKED",
                    "error_codes": ["ERR_ADVISORY_PROVENANCE_CHECK_INPUT"],
                    "errors": [
                        {
                            "code": "ERR_ADVISORY_PROVENANCE_CHECK_INPUT",
                            "message": "exactly one advisory artifact directory is required",
                            "evidence_path": "",
                        }
                    ],
                },
                sort_keys=True,
            )
        )
        return 2
    evidence_dir = Path(args[0]).resolve(strict=False)
    try:
        generic_adapter = importlib.import_module("depone.verify.adapters.generic")
        evidence_contract = importlib.import_module("depone.verify.evidence_contract")
        evidence = generic_adapter.read_evidence(str(evidence_dir))
        contract = json.loads(
            (evidence_dir / "evidence-contract.json").read_text(encoding="utf-8")
        )
        if not isinstance(contract, dict):
            raise ValueError("evidence-contract.json must contain an object")
        errors = evidence_contract.validate_advisory_provenance(evidence, contract)
    except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "kind": "orro-advisory-provenance-verdict",
            "schema_version": ADVISORY_PROVENANCE_SCHEMA_VERSION,
            "decision": "BLOCKED",
            "error_codes": ["ERR_ADVISORY_PROVENANCE_CHECK_BLOCKED"],
            "errors": [
                {
                    "code": "ERR_ADVISORY_PROVENANCE_CHECK_BLOCKED",
                    "message": str(exc),
                    "evidence_path": "evidence-contract.json",
                }
            ],
            "boundary": _boundary(),
            "status_note": _status_note(),
        }
        print(json.dumps(payload, sort_keys=True))
        return 2
    payload = {
        "kind": "orro-advisory-provenance-verdict",
        "schema_version": ADVISORY_PROVENANCE_SCHEMA_VERSION,
        "decision": "PASS" if not errors else "REFUTE",
        "error_codes": [error.code for error in errors],
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                "evidence_path": error.evidence_path,
            }
            for error in errors
        ],
        "boundary": _boundary(),
        "status_note": _status_note(),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not errors else 1


def _boundary() -> dict[str, bool]:
    return {
        "advisory_provenance_only": True,
        "asserts_correctness": False,
        "raises_assurance": False,
        "verifies_execution_evidence": False,
        "can_change_evidence_verdict": False,
        "executes_proofrun": False,
    }


def _status_note() -> str:
    return (
        "This verdict concerns tamper evidence and re-derivation of the advisory "
        "record's strongest claim from sealed bytes; it does not assess whether the "
        "direction or root cause is correct and does not raise assurance."
    )


if __name__ == "__main__":
    raise SystemExit(main())
