"""W1 committed-evidence fixtures re-derive their verdicts through Depone.

These tests load the bytes committed under ``fixtures/w1/`` and assert that the
separate Depone validator re-derives A1 (and the A2 demonstration) from them —
the same re-derivation ``scripts/revalidate_w1.py`` performs in G2. If a fixture
byte drifts from what Depone accepts, these fail.
"""

import json
import shutil
import unittest
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

from witnessd.canonical import canonical_hash

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "w1"
PROVENANCE_EVIDENCE_PATH = "fixtures/w1/capture-manifest.json"
_CONTRACT_FILES = (
    "evidence-contract.json",
    "git-diff-name-only.txt",
    "git-diff.patch",
    "exit-code.txt",
)


def _load(name: str) -> dict:
    with open(FIX / name, encoding="utf-8") as handle:
        return json.load(handle)


class TestW1Fixtures(unittest.TestCase):
    def test_a1_capture_manifest_valid(self):
        manifest = _load("capture-manifest.json")
        self.assertEqual(validate_capture_manifest(manifest), [])
        self.assertEqual(manifest["assurance"], "A1-local-observed")
        self.assertIsNone(manifest["prev_capture_hash"])

    def test_capture_chain_passes_and_reorder_blocked(self):
        m1 = _load("chain/capture-manifest-001.json")
        m2 = _load("chain/capture-manifest-002.json")
        self.assertEqual(m2["prev_capture_hash"], canonical_hash(m1))
        self.assertEqual(verify_capture_chain([m1, m2])["decision"], "pass")
        self.assertEqual(verify_capture_chain([m2, m1])["decision"], "blocked")

    def test_a2_demonstration_manifest_valid(self):
        # A2 fixture is a demonstration of the isolation gate (no uid-isolated
        # runner on this host); the recorded facts still establish a real
        # boundary, so Depone validates it as A2.
        manifest = _load("capture-manifest-a2.json")
        self.assertEqual(validate_capture_manifest(manifest), [])
        self.assertEqual(manifest["assurance"], "A2-isolated-observed")

    def test_runner_receipt_valid(self):
        receipt = _load("runner-receipt.json")
        self.assertEqual(validate_runner_receipt(receipt), [])
        self.assertEqual(receipt["runner_kind"], "manual")

    def test_evidence_contract_has_enforcement_directive(self):
        files = {
            name: (FIX / name).read_text(encoding="utf-8") for name in _CONTRACT_FILES
        }
        contract = json.loads(files["evidence-contract.json"])
        self.assertEqual(contract["schema_version"], "v105.verify_wedge")
        self.assertTrue(contract["allowed_touched_files"])
        context = EvidenceContext(
            run_id="w1-fixtures",
            files=[
                EvidenceFile(path=name, content=content, sha256=canonical_hash(content))
                for name, content in files.items()
            ],
            raw={},
        )
        self.assertEqual(validate_evidence_contract(context), [])


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestW1SignedFixtures(unittest.TestCase):
    def _pub(self) -> str:
        return str(FIX / "keys" / "operator.pub")

    def test_signed_bundle_verifies_and_ingests(self):
        bundle = _load("bundle.json")
        self.assertTrue(verify_signed_bundle(bundle, self._pub()))
        artifact_paths = {
            "capture-manifest": str(FIX / "capture-manifest.json"),
            "observer-capture": str(FIX / "observer-capture.json"),
            "runner-receipt": str(FIX / "runner-receipt.json"),
        }
        verdict = ingest_signed_evidence_bundle(
            bundle, self._pub(), artifact_paths, otel_spans=bundle["otel_spans"]
        )
        self.assertTrue(verdict["signature_verified"])
        self.assertEqual(verdict["decision"], "pass")

    def test_trusted_observer_provenance_validates(self):
        manifest = _load("capture-manifest.json")
        provenance = _load("provenance.json")
        errors = validate_trusted_observer_provenance(
            manifest,
            evidence_path=PROVENANCE_EVIDENCE_PATH,
            provenance=[provenance],
            public_key_path=self._pub(),
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
