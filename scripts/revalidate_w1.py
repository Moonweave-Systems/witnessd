"""G2 — re-derive the W1 verdicts from committed evidence bytes via Depone.

witnessd never verifies its own evidence. This harness loads only the bytes
committed under ``fixtures/w1/`` and hands them to the installed Depone
(non-executing) validator, asserting that Depone independently re-derives every
W1 claim: A1 and strict real A2, an append-only capture
chain with reorder/tamper blocked, an operator-key signed bundle whose subjects
re-hash from the same files, a valid runner-receipt, trusted-observer-provenance
bound to the exact manifest, and an evidence-contract carrying an enforcement
directive. It also asserts the negative direction inline: a forged ``A3-*``
assurance fails signature verification.

The A2 assurance claim is no longer demonstration-only: after W12, the committed
``fixtures/w1/capture-manifest-a2.json`` bytes must prove an observer-launched
uid boundary with a dedicated observer uid and a runner-unwritable observer dir.

Run with:

    PYTHONPATH=/path/to/depone python3 scripts/revalidate_w1.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import (
    ingest_signed_evidence_bundle,
    verify_capture_chain,
)
from depone.agent_fabric.isolation import (
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
    verify_isolation_boundary,
)
from depone.agent_fabric.observer_provenance import (
    validate_trusted_observer_provenance,
)
from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.sign import verify_signed_bundle
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.evidence_contract import validate_evidence_contract
from scripts.revalidate_w12 import (
    assert_runner_writable_observer_dir_blocks,
    assert_strict_real_a2,
)

FIX = REPO_ROOT / "fixtures" / "w1"
NEG = FIX / "negative"
PROVENANCE_EVIDENCE_PATH = "fixtures/w1/capture-manifest.json"
W12_REAL_A2 = REPO_ROOT / "fixtures" / "w12" / "capture-manifest.json"
CONTRACT_FILES = (
    "evidence-contract.json",
    "git-diff-name-only.txt",
    "git-diff.patch",
    "exit-code.txt",
)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _load_negative(name: str) -> dict:
    return json.loads((NEG / name).read_text(encoding="utf-8"))


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
    manifest_bytes = (FIX / "capture-manifest-a2.json").read_bytes()
    w12_bytes = W12_REAL_A2.read_bytes()
    _require(
        manifest_bytes == w12_bytes,
        "W1 A2 manifest must be sourced byte-for-byte from the W12 real A2 manifest",
    )
    w12_manifest = json.loads(W12_REAL_A2.read_text(encoding="utf-8"))
    assert_strict_real_a2(w12_manifest)
    assert_runner_writable_observer_dir_blocks(w12_manifest)
    _require(
        validate_capture_manifest(manifest) == [],
        "A2 capture-manifest must validate with no errors",
    )
    _require(
        manifest["assurance"] == "A2-isolated-observed",
        f"A2 manifest assurance must be A2-isolated-observed, got {manifest['assurance']!r}",
    )
    isolation = manifest.get("isolation")
    if not isinstance(isolation, dict):
        raise AssertionError("A2 manifest must include isolation facts")
    verified = verify_isolation_boundary(isolation)
    _require(
        verified.get("boundary") is True,
        f"A2 isolation facts must establish a boundary: {verified!r}",
    )
    _require(
        verified.get("model") == UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
        "A2 manifest must use the observer-launched uid isolation model",
    )
    _require(
        verified.get("runner_uid") != verified.get("observer_uid"),
        "A2 runner and observer uids must differ",
    )
    _require(verified.get("runner_uid") != 0, "A2 root runner uid is forbidden")
    _require(
        verified.get("observer_dir_writable_by_runner") is False,
        "A2 observer_dir must be proven not writable by the runner",
    )
    _require(
        isolation.get("observer_dir_mode") == "0700",
        f"A2 observer_dir_mode must be 0700, got {isolation.get('observer_dir_mode')!r}",
    )
    _require(
        verified.get("observer_launched") is True,
        "A2 runner must be observer-launched",
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


def _revalidate_negatives(public_key_path: str) -> None:
    """Each committed tamper fixture must be rejected by Depone, not witnessd.

    A single forged byte-group per fixture: a stale observer_capture_hash, a stale
    observer_capture.source_fixture_hash, an out-of-envelope touched file, and a
    bundle whose top-level assurance is inflated to a forged A3 the signature does
    not cover. Depone surfaces the first three as manifest errors and the last as a
    failed signature.
    """

    hash_mismatch = validate_capture_manifest(
        _load_negative("observer_capture_hash_mismatch.json")
    )
    _require(
        "observer_capture_hash mismatch" in hash_mismatch,
        f"tampered observer_capture_hash must be detected, got {hash_mismatch!r}",
    )
    stale = validate_capture_manifest(_load_negative("stale_source_fixture_hash.json"))
    _require(
        "observer_capture.source_fixture_hash is stale" in stale,
        f"stale observer source_fixture_hash must be detected, got {stale!r}",
    )
    touched = validate_capture_manifest(_load_negative("unexpected_touched_files.json"))
    _require(
        any("unexpected touched files" in error for error in touched),
        f"out-of-envelope touched file must be detected, got {touched!r}",
    )
    forged = _load_negative("forged_a3.json")
    _require(
        forged["assurance"] == "A3-fabricated-observed",
        "forged_a3 fixture must actually claim a forged A3 assurance",
    )
    _require(
        not verify_signed_bundle(forged, public_key_path),
        "forged A3 bundle must fail signature verification",
    )


def main() -> int:
    public_key_path = str(FIX / "keys" / "operator.pub")
    _revalidate_a1(public_key_path)
    _revalidate_a2()
    _revalidate_chain()
    _revalidate_bundle(public_key_path)
    _revalidate_receipt()
    _revalidate_contract()
    _revalidate_negatives(public_key_path)
    print("W1 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
