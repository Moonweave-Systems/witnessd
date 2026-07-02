#!/usr/bin/env python3
"""Re-derive W10 live-agent evidence from committed fixture bytes."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

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

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "w10"
EVIDENCE = FIX / "evidence"
PUBLIC_KEY = FIX / "keys" / "operator.pub"
CONTRACT_FILES = (
    "evidence-contract.json",
    "git-diff-name-only.txt",
    "git-diff.patch",
    "exit-code.txt",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_no_private_key_material() -> None:
    for path in FIX.rglob("*"):
        if path.is_file() and b"PRIVATE KEY" in path.read_bytes():
            raise AssertionError(f"private key material must not be committed: {path}")


def _assert_manifest_and_provenance() -> dict[str, Any]:
    manifest_path = EVIDENCE / "capture-manifest.json"
    manifest = _load(manifest_path)
    errors = validate_capture_manifest(manifest)
    _require(errors == [], f"W10 capture-manifest invalid: {errors}")
    _require(
        manifest["assurance"] == "A1-local-observed",
        f"W10 live lane must be A1-local-observed, got {manifest['assurance']!r}",
    )
    _require(
        manifest["evidence_mode"] == "contemporaneous",
        f"W10 live lane must be contemporaneous, got {manifest['evidence_mode']!r}",
    )
    chain = verify_capture_chain([manifest])
    _require(chain["decision"] == "pass", f"W10 capture chain failed: {chain}")
    provenance = _load(EVIDENCE / "provenance.json")
    provenance_errors = validate_trusted_observer_provenance(
        manifest,
        evidence_path=str(manifest_path),
        provenance=[provenance],
        public_key_path=str(PUBLIC_KEY),
    )
    _require(provenance_errors == [], f"W10 provenance invalid: {provenance_errors}")
    return manifest


def _assert_bundle() -> None:
    bundle = _load(EVIDENCE / "bundle.json")
    _require(
        verify_signed_bundle(bundle, str(PUBLIC_KEY)) is True,
        "W10 signed evidence bundle signature must verify",
    )
    ingest = ingest_signed_evidence_bundle(
        bundle,
        str(PUBLIC_KEY),
        {
            "capture-manifest": str(EVIDENCE / "capture-manifest.json"),
            "observer-capture": str(EVIDENCE / "observer-capture.json"),
            "runner-receipt": str(EVIDENCE / "runner-receipt.json"),
        },
        otel_spans=bundle.get("otel_spans"),
    )
    _require(ingest["decision"] == "pass", f"W10 bundle ingest failed: {ingest}")
    _require(ingest["signature_verified"] is True, "W10 bundle signature not verified")

    forged = copy.deepcopy(bundle)
    forged["assurance"] = "A3-fabricated-observed"
    forged["statement"]["predicate"]["assurance"] = "A3-fabricated-observed"
    _require(
        verify_signed_bundle(forged, str(PUBLIC_KEY)) is False,
        "forged W10 A3 assurance must fail signature verification",
    )


def _assert_runner_receipt() -> dict[str, Any]:
    receipt = _load(EVIDENCE / "runner-receipt.json")
    receipt_errors = validate_runner_receipt(receipt)
    _require(receipt_errors == [], f"W10 runner receipt invalid: {receipt_errors}")
    _require(receipt["runner_kind"] == "codex-cli", "W10 must use codex-cli runner_kind")
    _require(receipt["exit_code"] == 0, f"W10 adapter exit must be 0, got {receipt['exit_code']}")
    _require(
        receipt["touched_files"] == ["wordscore/core.py"],
        f"W10 touched files must be the generated code file only: {receipt['touched_files']}",
    )
    invocation = receipt.get("invocation", [])
    _require("exec" in invocation, "W10 codex invocation must use exec")
    _require("--output-last-message" in invocation, "W10 codex invocation must record transcript")
    return receipt


def _assert_contract() -> None:
    files = [
        EvidenceFile(
            path=name,
            content=(EVIDENCE / name).read_text(encoding="utf-8"),
            sha256=hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(),
        )
        for name in CONTRACT_FILES
    ]
    context = EvidenceContext(run_id="w10-revalidate", files=files, raw={})
    errors = validate_evidence_contract(context)
    _require(errors == [], f"W10 evidence contract invalid: {errors}")


def _assert_nontrivial_diff() -> None:
    name_only = (EVIDENCE / "git-diff-name-only.txt").read_text(encoding="utf-8").splitlines()
    _require(name_only == ["wordscore/core.py"], f"W10 diff name-only unexpected: {name_only}")
    patch = (EVIDENCE / "git-diff.patch").read_text(encoding="utf-8")
    additions = [
        line for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    ]
    _require("diff --git a/wordscore/core.py b/wordscore/core.py" in patch, "W10 patch must target wordscore/core.py")
    _require(len(additions) >= 4, f"W10 patch must contain generated code additions, got {len(additions)}")
    _require("echo " not in patch.lower(), "W10 patch must not be echo-like")
    _require("__pycache__" not in patch, "W10 patch must not include Python cache files")


def _assert_prompt_provenance() -> None:
    prompt = (FIX / "PROMPT.md").read_text(encoding="utf-8")
    _require("adapter: codex" in prompt, "W10 prompt provenance must record codex adapter")
    _require("runner_kind: codex-cli" in prompt, "W10 prompt provenance must record codex-cli")
    _require("--max-tokens" in prompt and "--max-usd" in prompt and "--max-depth" in prompt, "W10 prompt provenance must record budget flags")
    _require("post_run_tests_exit_code: 0" in prompt, "W10 prompt provenance must record post-run tests")
    _require((FIX / "POST_RUN_TESTS.txt").exists(), "W10 must include post-run test output")


def main() -> int:
    _require(FIX.is_dir(), "fixtures/w10 must exist")
    _require(PUBLIC_KEY.exists(), "W10 public key fixture missing")
    _assert_no_private_key_material()
    _assert_manifest_and_provenance()
    _assert_bundle()
    _assert_runner_receipt()
    _assert_contract()
    _assert_nontrivial_diff()
    _assert_prompt_provenance()
    print("W10 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
