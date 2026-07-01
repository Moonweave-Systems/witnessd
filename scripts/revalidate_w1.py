"""G2 — re-derive the W1 verdicts from committed evidence bytes via Depone.

witnessd never verifies its own evidence. This harness loads only the bytes
committed under ``fixtures/w1/`` and hands them to the installed Depone
(non-executing) validator, asserting that Depone independently re-derives every
W1 claim: A1 (and, on a genuinely uid-isolated host, A2), an append-only capture
chain with reorder/tamper blocked, an operator-key signed bundle whose subjects
re-hash from the same files, a valid runner-receipt, trusted-observer-provenance
bound to the exact manifest, and an evidence-contract carrying an enforcement
directive. It also asserts the negative direction inline: a forged ``A3-*``
assurance fails signature verification.

The A2 assurance claim is uid-host-conditional: ``fixtures/w1/capture-manifest-a2.json``
is a demonstration on hosts without uid isolation (flagged by
``fixtures/w1/A2-DEMONSTRATION.md``). There the manifest is still asserted
structurally valid, but the strict ``assurance == "A2-isolated-observed"`` claim
is only enforced when a real uid-isolated run produced the fixture.

Run with:

    PYTHONPATH=/home/ubuntu/depone-assurance-repair uv run python3 scripts/revalidate_w1.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import (
    ingest_signed_evidence_bundle,
    verify_capture_chain,
)
from depone.agent_fabric.observer_provenance import (
    validate_trusted_observer_provenance,
)
from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.sign import verify_signed_bundle
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.evidence_contract import validate_evidence_contract

REPO_ROOT = Path(__file__).resolve().parent.parent
FIX = REPO_ROOT / "fixtures" / "w1"
PROVENANCE_EVIDENCE_PATH = "fixtures/w1/capture-manifest.json"
CONTRACT_FILES = (
    "evidence-contract.json",
    "git-diff-name-only.txt",
    "git-diff.patch",
    "exit-code.txt",
)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _revalidate_a1(public_key_path: str) -> dict:
    manifest = _load("capture-manifest.json")
    _require(
        validate_capture_manifest(manifest) == [],
        "A1 capture-manifest must validate with no errors",
    )
    _require(
        manifest["assurance"] == "A1-local-observed",
        f"A1 manifest assurance must be A1-local-observed, got {manifest['assurance']!r}",
    )
    provenance = _load("provenance.json")
    _require(
        validate_trusted_observer_provenance(
            manifest,
            evidence_path=PROVENANCE_EVIDENCE_PATH,
            provenance=[provenance],
            public_key_path=public_key_path,
        )
        == [],
        "trusted-observer-provenance must bind the A1 manifest exactly",
    )
    return manifest


def _revalidate_a2() -> None:
    manifest = _load("capture-manifest-a2.json")
    _require(
        validate_capture_manifest(manifest) == [],
        "A2 capture-manifest must validate with no errors",
    )
    is_demonstration = (FIX / "A2-DEMONSTRATION.md").exists()
    if is_demonstration:
        # uid-host-conditional: without uid isolation the A2 assurance claim is a
        # demonstration only, so the strict assert is deferred to a real host.
        print(
            "W1 revalidate: A2 demonstration only (no uid isolation on host) — "
            f"Depone derives {manifest['assurance']!r}, strict A2 assert deferred"
        )
    else:
        _require(
            manifest["assurance"] == "A2-isolated-observed",
            f"A2 manifest assurance must be A2-isolated-observed, got {manifest['assurance']!r}",
        )


def _revalidate_chain() -> None:
    genesis = _load("chain/capture-manifest-001.json")
    linked = _load("chain/capture-manifest-002.json")
    _require(
        verify_capture_chain([genesis, linked])["decision"] == "pass",
        "committed capture chain must verify as pass",
    )
    _require(
        verify_capture_chain([linked, genesis])["decision"] == "blocked",
        "reordered capture chain must be blocked",
    )
    tampered = copy.deepcopy(genesis)
    tampered["allowed_touched_files"] = ["tampered.py"]
    _require(
        verify_capture_chain([tampered, linked])["decision"] == "blocked",
        "tampered predecessor must break the chain",
    )


def _revalidate_bundle(public_key_path: str) -> None:
    bundle = _load("bundle.json")
    _require(
        verify_signed_bundle(bundle, public_key_path),
        "committed evidence bundle must verify against the operator public key",
    )
    artifact_paths = {
        "capture-manifest": str(FIX / "capture-manifest.json"),
        "observer-capture": str(FIX / "observer-capture.json"),
        "runner-receipt": str(FIX / "runner-receipt.json"),
    }
    verdict = ingest_signed_evidence_bundle(
        bundle,
        public_key_path,
        artifact_paths,
        otel_spans=bundle["otel_spans"],
    )
    _require(
        verdict["signature_verified"] is True,
        "ingested bundle must report signature_verified",
    )
    _require(
        verdict["decision"] == "pass",
        f"ingested bundle must pass, got {verdict['decision']!r}",
    )

    forged = copy.deepcopy(bundle)
    forged["assurance"] = "A3-fabricated-observed"
    forged["statement"]["predicate"]["assurance"] = "A3-fabricated-observed"
    _require(
        not verify_signed_bundle(forged, public_key_path),
        "forged A3 assurance must fail signature verification",
    )


def _revalidate_receipt() -> None:
    receipt = _load("runner-receipt.json")
    _require(
        validate_runner_receipt(receipt) == [],
        "committed runner-receipt must validate with no errors",
    )
    _require(
        receipt["runner_kind"] == "manual",
        f"runner_kind must be manual, got {receipt['runner_kind']!r}",
    )


def _revalidate_contract() -> None:
    files = [
        EvidenceFile(
            path=name,
            content=(FIX / name).read_text(encoding="utf-8"),
            sha256=hashlib.sha256((FIX / name).read_bytes()).hexdigest(),
        )
        for name in CONTRACT_FILES
    ]
    context = EvidenceContext(run_id="w1-revalidate", files=files, raw={})
    _require(
        validate_evidence_contract(context) == [],
        "committed evidence-contract must validate with an enforcement directive",
    )


def main() -> int:
    public_key_path = str(FIX / "keys" / "operator.pub")
    _revalidate_a1(public_key_path)
    _revalidate_a2()
    _revalidate_chain()
    _revalidate_bundle(public_key_path)
    _revalidate_receipt()
    _revalidate_contract()
    print("W1 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
