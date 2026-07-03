# W6 Keyless Signing Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a fail-closed keyless signing seam and non-trusting keyless fixture linter without letting keyless metadata become verified evidence.

**Architecture:** W6a is readiness only. Depone gets a separate keyless fixture linter that never returns `signature_verified=true` and never routes through `ingest_signed_evidence_bundle`; witnessd gets a signing-profile seam whose default remains operator-key and whose keyless profile fails closed with `ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED`. The external-team-pilot production gate is already open with 5/5 evidence recorded; live Fulcio/Rekor verification and keyless ingest are still deferred to W6b because an approved dependency/network verifier design does not exist yet.

**Tech Stack:** Python 3.10+, stdlib JSON/hash/base64/path/unittest, existing `openssl` CLI only for the unchanged operator-key path. No new dependency, network call, Sigstore CLI, OIDC exchange, Rekor lookup, public assurance claim, or production-gate opening in W6a.

---

## Review Outcome Incorporated

Two independent read-only reviews of the first draft returned `BLOCK`. The plan below incorporates those blockers:

- Keyless fixture metadata must not produce `signature_verified=true`.
- `ingest_signed_evidence_bundle` remains operator-key-only in W6a.
- Keyless profile selection is not controlled by a caller boolean.
- The W6 keyless fixture binds a real Depone-valid capture manifest and the revalidator checks that with `validate_capture_manifest`.
- A self-consistent forged keyless bundle is rejected by pinned fixture hash in the linter path, but this is still fixture integrity, not signature verification.

## Status And Stop Conditions

- Current witnessd baseline: `00a5c9a`.
- Current depone baseline: `e08de54`.
- `fixtures/key-rotation/operator-key-archive.json` must keep `production_gate.status == "open"` throughout W6a; do not mutate the archive, operator review, or recorded evidence.
- Stop immediately if any implementation modifies `depone/agent_fabric/evidence_substrate.py::ingest_signed_evidence_bundle` to accept keyless bundles.
- Stop immediately if any implementation emits or accepts `A3`, deprecates operator-key signing, treats local dogfood as production evidence, or adds a dependency/network verifier without explicit approval.

## Non-Negotiable Invariants

- Assurance ceiling remains `A2-isolated-observed`.
- Operator-key signing remains default and verified by `verify_signed_bundle`.
- Keyless fixture linting is not authentication; it returns `signature_verified: false`, `trusts_external_signature: false`, `keyless_identity: false`, and `transparency_logged: false`.
- Keyless proof acceptance must eventually be a Depone decision based on real cryptographic/transparency verification, not witnessd self-report or self-consistent fixture metadata.
- W6a production runtime cannot emit keyless evidence.

## File Structure

```
/home/ubuntu/moonweave/depone/
  depone/agent_fabric/keyless.py              # NEW: non-trusting fixture linter only.
  tests/test_agent_fabric_keyless_lint.py     # NEW: linter, anti-forgery, operator verifier rejection.
  depone/fixtures/agent_fabric/keyless/
    keyless-capture-manifest.json             # Real Depone-valid capture manifest copied from existing fixture.
    keyless-bundle.json                       # Keyless-shaped metadata bundle.
    keyless-bundle.sha256                     # Pinned raw fixture hash for linter.
    negative-forged-self-consistent.json      # Same-shape but unpinned forged metadata.
    negative-fake-subject.json                # Valid embedded capture but mismatched statement subject digest.
    negative-assurance-upgrade.json           # A3 attempt.

/home/ubuntu/moonweave/witnessd/
  witnessd/signing_profile.py                 # NEW: operator default; keyless live verifier unimplemented in W6a.
  witnessd/substrate.py                       # MODIFY: behavior-preserving profile seam.
  tests/test_signing_profile.py               # NEW: default + fail-closed keyless.
  tests/test_substrate_keyless_guard.py       # NEW: build_bundle cannot emit keyless.
  scripts/revalidate_w6_keyless.py            # NEW: cross-repo W6a gate.
  docs/plans/README.md                        # MODIFY: W6a row.
  SPEC.md                                     # MODIFY: W6a/W6b split.
```

## Explicit Non-Changes

- Do not modify `/home/ubuntu/moonweave/depone/depone/agent_fabric/evidence_substrate.py` in W6a.
- Do not add keyless routing to `ingest_signed_evidence_bundle`.
- Do not add a `production_gate_open=True` boolean, env var, or CLI flag.
- Do not add live Sigstore/Fulcio/Rekor verification in W6a.

## Task 0: Baseline Gate

**Files:**
- Read: `/home/ubuntu/moonweave/witnessd/fixtures/key-rotation/operator-key-archive.json`
- Read: `/home/ubuntu/moonweave/witnessd/scripts/revalidate_key_rotation.py`

- [ ] **Step 1: Confirm baselines**

```bash
cd /home/ubuntu/moonweave/witnessd
git status --short --branch
git rev-parse --short HEAD

cd /home/ubuntu/moonweave/depone
git status --short --branch
git rev-parse --short HEAD
```

Expected: witnessd is `00a5c9a` or a descendant, depone is `e08de54` or a descendant. Preserve any unrelated existing working-tree changes.

- [ ] **Step 2: Confirm production gate is open and recorded**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_key_rotation.py
python3 - <<'PY'
import json
from pathlib import Path
archive = json.loads(Path("fixtures/key-rotation/operator-key-archive.json").read_text())
gate = archive["production_gate"]
assert gate["status"] == "open", gate
assert len(gate["required_evidence"]) == 5, gate
assert all(item["status"] == "recorded" for item in gate["required_evidence"]), gate
print("production gate open with 5 recorded evidence entries")
PY
```

Expected: `key rotation revalidate: PASS` and `production gate open with 5 recorded evidence entries`.

## Task 1: Depone Keyless Linter Tests

**Files:**
- Create: `/home/ubuntu/moonweave/depone/tests/test_agent_fabric_keyless_lint.py`
- Create later: `/home/ubuntu/moonweave/depone/depone/agent_fabric/keyless.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import json
import unittest
from pathlib import Path

from depone._resources import resource_text
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.keyless import (
    SIGNING_STATUS_KEYLESS_FULCIO_REKOR,
    keyless_fixture_boundary,
    lint_keyless_bundle_fixture,
)
from depone.agent_fabric.sign import verify_signed_bundle


class AgentFabricKeylessLintTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def _fixture(self, name: str) -> dict[str, object]:
        return json.loads(resource_text(f"fixtures/agent_fabric/keyless/{name}"))

    def _pinned_hash(self) -> str:
        return resource_text("fixtures/agent_fabric/keyless/keyless-bundle.sha256").strip()

    def test_fixture_subject_is_real_depone_valid_capture_manifest(self) -> None:
        capture = json.loads(resource_text("fixtures/agent_fabric/keyless/keyless-capture-manifest.json"))
        self.assertEqual(validate_capture_manifest(capture), [])

    def test_linter_lints_pinned_shape_but_never_trusts_signature(self) -> None:
        report = lint_keyless_bundle_fixture(self._fixture("keyless-bundle.json"), expected_bundle_sha256=self._pinned_hash())
        self.assertEqual(report["decision"], "lint_passed")
        self.assertEqual(report["signing_status"], SIGNING_STATUS_KEYLESS_FULCIO_REKOR)
        self.assertFalse(report["signature_verified"])
        self.assertFalse(report["boundary"]["trusts_external_signature"])
        self.assertFalse(report["boundary"]["raises_assurance"])

    def test_operator_verifier_rejects_keyless_bundle(self) -> None:
        bundle = self._fixture("keyless-bundle.json")
        public_key_path = Path("/home/ubuntu/moonweave/witnessd/fixtures/w1/keys/operator.pub")
        self.assertTrue(public_key_path.is_file())
        self.assertFalse(verify_signed_bundle(bundle, str(public_key_path)))

    def test_self_consistent_forged_bundle_is_blocked_by_pin(self) -> None:
        report = lint_keyless_bundle_fixture(
            self._fixture("negative-forged-self-consistent.json"),
            expected_bundle_sha256=self._pinned_hash(),
        )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("fixture hash mismatch", report["reasons"])

    def test_valid_embedded_capture_with_fake_subject_digest_is_blocked(self) -> None:
        report = lint_keyless_bundle_fixture(
            self._fixture("negative-fake-subject.json"),
            expected_bundle_sha256=None,
        )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("subject digest mismatch", report["reasons"])

    def test_assurance_upgrade_is_blocked(self) -> None:
        report = lint_keyless_bundle_fixture(
            self._fixture("negative-assurance-upgrade.json"),
            expected_bundle_sha256=None,
        )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("assurance exceeds A2", report["reasons"])

    def test_boundary_is_not_operator_key(self) -> None:
        boundary = keyless_fixture_boundary()
        self.assertFalse(boundary["operator_key"])
        self.assertFalse(boundary["keyless_identity"])
        self.assertFalse(boundary["transparency_logged"])
        self.assertTrue(boundary["claimed_keyless_identity"])
        self.assertTrue(boundary["claimed_rekor_metadata"])
        self.assertFalse(boundary["raises_assurance"])
```

- [ ] **Step 2: Run and confirm red**

```bash
cd /home/ubuntu/moonweave/depone
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_fabric_keyless_lint -v
```

Expected: import failure for `depone.agent_fabric.keyless`.

## Task 2: Depone Non-Trusting Keyless Linter

**Files:**
- Create: `/home/ubuntu/moonweave/depone/depone/agent_fabric/keyless.py`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/keyless-capture-manifest.json`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/keyless-bundle.json`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/keyless-bundle.sha256`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/negative-forged-self-consistent.json`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/negative-fake-subject.json`
- Create: `/home/ubuntu/moonweave/depone/depone/fixtures/agent_fabric/keyless/negative-assurance-upgrade.json`

- [ ] **Step 1: Add `keyless.py`**

```python
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from depone.agent_fabric.capture_bridge import validate_capture_manifest

SIGNING_STATUS_KEYLESS_FULCIO_REKOR = "keyless-fulcio-rekor-fixture-untrusted"
KEYLESS_FIXTURE_SCHEME = "DSSE-Sigstore-Fulcio-Rekor-offline-fixture-untrusted"
_ASSURANCE_CEILING = {"A0-claims-only", "A1-local-observed", "A2-isolated-observed"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def keyless_fixture_boundary() -> dict[str, Any]:
    return {
        "scheme": KEYLESS_FIXTURE_SCHEME,
        "operator_key": False,
        "public_verifiable": False,
        "keyless_identity": False,
        "transparency_logged": False,
        "claimed_keyless_identity": True,
        "claimed_rekor_metadata": True,
        "raises_assurance": False,
        "fixture_only": True,
        "trusts_external_signature": False,
        "note": (
            "Offline shape lint only. This is not Fulcio chain verification, "
            "Rekor inclusion verification, or a trusted signature result."
        ),
    }


def _base_report(decision: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "decision": decision,
        "reasons": reasons,
        "signing_status": SIGNING_STATUS_KEYLESS_FULCIO_REKOR,
        "signature_verified": False,
        "boundary": {
            "raises_assurance": False,
            "trusts_external_signature": False,
        },
    }


def _capture_subject_digest(statement: dict[str, Any]) -> str | None:
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return None
    for item in subjects:
        if not isinstance(item, dict) or item.get("name") != "depone-capture-manifest":
            continue
        digest = item.get("digest")
        if isinstance(digest, dict) and isinstance(digest.get("sha256"), str):
            return digest["sha256"]
    return None


def lint_keyless_bundle_fixture(
    bundle: dict[str, Any],
    *,
    expected_bundle_sha256: str | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(bundle, dict):
        return _base_report("blocked", ["bundle must be an object"])
    if expected_bundle_sha256 is not None and _sha256_json(bundle) != expected_bundle_sha256:
        reasons.append("fixture hash mismatch")
    if bundle.get("signing_status") != SIGNING_STATUS_KEYLESS_FULCIO_REKOR:
        reasons.append("signing_status mismatch")
    if bundle.get("signature_boundary") != keyless_fixture_boundary():
        reasons.append("signature_boundary mismatch")
    if bundle.get("assurance") not in _ASSURANCE_CEILING:
        reasons.append("assurance exceeds A2")

    statement = bundle.get("statement")
    envelope = bundle.get("dsse_envelope")
    if not isinstance(statement, dict) or not isinstance(envelope, dict):
        reasons.append("statement and dsse_envelope must be objects")
    else:
        payload = envelope.get("payload")
        expected_payload = base64.b64encode(_canonical_json(statement).encode("utf-8")).decode("ascii")
        if envelope.get("payloadType") != "application/vnd.in-toto+json":
            reasons.append("unsupported payloadType")
        if payload != expected_payload:
            reasons.append("payload does not match statement")
        predicate = statement.get("predicate")
        if not isinstance(predicate, dict):
            reasons.append("predicate must be an object")
        else:
            if bundle.get("assurance") != predicate.get("assurance"):
                reasons.append("top-level assurance mismatch")
            if predicate.get("assurance") not in _ASSURANCE_CEILING:
                reasons.append("assurance exceeds A2")
            boundary = predicate.get("boundary")
            if not isinstance(boundary, dict):
                reasons.append("predicate boundary must be an object")
            elif boundary.get("raises_assurance") is not False:
                reasons.append("predicate raises assurance")
        signatures = envelope.get("signatures")
        if not isinstance(signatures, list) or len(signatures) != 1:
            reasons.append("fixture must contain exactly one keyless metadata record")
        elif not isinstance(signatures[0], dict):
            reasons.append("signature metadata must be an object")
        else:
            metadata = signatures[0]
            if metadata.get("sig") != "fixture-metadata-not-a-cryptographic-signature":
                reasons.append("fixture signature marker mismatch")
            rekor = metadata.get("rekor")
            if not isinstance(rekor, dict) or not rekor.get("uuid"):
                reasons.append("rekor metadata missing uuid")

    subject_capture = bundle.get("subject_capture_manifest")
    if not isinstance(subject_capture, dict):
        reasons.append("subject_capture_manifest must be embedded for fixture lint")
    else:
        if validate_capture_manifest(subject_capture) != []:
            reasons.append("subject capture manifest invalid")
        if isinstance(statement, dict):
            actual_subject_digest = _capture_subject_digest(statement)
            expected_subject_digest = _sha256_json(subject_capture)
            if actual_subject_digest != expected_subject_digest:
                reasons.append("subject digest mismatch")

    return _base_report("blocked" if reasons else "lint_passed", reasons)
```

- [ ] **Step 2: Generate fixtures from an existing Depone-valid capture manifest**

```bash
cd /home/ubuntu/moonweave/depone
python3 - <<'PY'
import base64
import hashlib
import json
from pathlib import Path

out = Path("depone/fixtures/agent_fabric/keyless")
out.mkdir(parents=True, exist_ok=True)
source = Path("depone/fixtures/agent_fabric/capture_manifest_v126_governed_utf8.json")
capture = json.loads(source.read_text(encoding="utf-8"))

def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

(out / "keyless-capture-manifest.json").write_text(canonical_json(capture), encoding="utf-8")
capture_digest = hashlib.sha256(canonical_json(capture).encode("utf-8")).hexdigest()

boundary = {
    "scheme": "DSSE-Sigstore-Fulcio-Rekor-offline-fixture-untrusted",
    "operator_key": False,
    "public_verifiable": False,
    "keyless_identity": False,
    "transparency_logged": False,
    "claimed_keyless_identity": True,
    "claimed_rekor_metadata": True,
    "raises_assurance": False,
    "fixture_only": True,
    "trusts_external_signature": False,
    "note": "Offline shape lint only. This is not Fulcio chain verification, Rekor inclusion verification, or a trusted signature result.",
}
statement = {
    "_type": "https://in-toto.io/Statement/v1",
    "subject": [
        {
            "name": "depone-capture-manifest",
            "digest": {
                "sha256": capture_digest
            },
        }
    ],
    "predicateType": "https://depone.dev/attestations/evidence/v1",
    "predicate": {
        "schema_version": "1.0",
        "source_kind": capture["kind"],
        "assurance": capture["assurance"],
        "decision": capture["decision"],
        "boundary": {
            "raises_assurance": False,
            "signed": False,
            "signing_status": "keyless-fulcio-rekor-fixture-untrusted",
        },
    },
}
bundle = {
    "kind": "depone-keyless-fixture-bundle",
    "schema_version": "1.0",
    "statement": statement,
    "subject_capture_manifest": capture,
    "dsse_envelope": {
        "payloadType": "application/vnd.in-toto+json",
        "payload": base64.b64encode(canonical_json(statement).encode("utf-8")).decode("ascii"),
        "signatures": [
            {
                "keyid": "fulcio-rekor-fixture-2026-07-02",
                "sig": "fixture-metadata-not-a-cryptographic-signature",
                "cert": {
                    "issuer": "https://token.actions.githubusercontent.com",
                    "subject": "repo:Moonweave-Systems/witnessd:environment:external-team-pilot",
                },
                "rekor": {
                    "log_index": 1,
                    "uuid": "witnessd-w6-keyless-fixture-0001",
                    "integrated_time": "2026-07-02T00:10:00Z",
                },
            }
        ],
    },
    "assurance": capture["assurance"],
    "signing_status": "keyless-fulcio-rekor-fixture-untrusted",
    "signature_boundary": boundary,
}
(out / "keyless-bundle.json").write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
digest = hashlib.sha256(canonical_json(bundle).encode("utf-8")).hexdigest()
(out / "keyless-bundle.sha256").write_text(digest + "\n", encoding="utf-8")

forged = json.loads(json.dumps(bundle))
forged["dsse_envelope"]["signatures"][0]["rekor"]["uuid"] = "forged-self-consistent"
(out / "negative-forged-self-consistent.json").write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")

fake_subject = json.loads(json.dumps(bundle))
fake_subject["statement"]["subject"][0]["digest"]["sha256"] = "0" * 64
fake_subject["dsse_envelope"]["payload"] = base64.b64encode(canonical_json(fake_subject["statement"]).encode("utf-8")).decode("ascii")
(out / "negative-fake-subject.json").write_text(json.dumps(fake_subject, indent=2, sort_keys=True) + "\n", encoding="utf-8")

upgraded = json.loads(json.dumps(bundle))
upgraded["assurance"] = "A3-keyless-signed-rekor"
upgraded["statement"]["predicate"]["assurance"] = "A3-keyless-signed-rekor"
upgraded["dsse_envelope"]["payload"] = base64.b64encode(canonical_json(upgraded["statement"]).encode("utf-8")).decode("ascii")
(out / "negative-assurance-upgrade.json").write_text(json.dumps(upgraded, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(digest)
PY
```

Expected: prints a 64-character sha256 digest.

- [ ] **Step 3: Run targeted tests**

```bash
cd /home/ubuntu/moonweave/depone
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_fabric_keyless_lint tests.test_agent_fabric_sign -v
```

Expected: pass. `verify_signed_bundle` still rejects the keyless fixture.

- [ ] **Step 4: Commit**

```bash
git add depone/agent_fabric/keyless.py depone/fixtures/agent_fabric/keyless tests/test_agent_fabric_keyless_lint.py
git commit -m "feat: add non-trusting keyless fixture linter"
```

## Task 3: witnessd Signing Profile Seam

**Files:**
- Create: `/home/ubuntu/moonweave/witnessd/witnessd/signing_profile.py`
- Modify: `/home/ubuntu/moonweave/witnessd/witnessd/substrate.py`
- Create: `/home/ubuntu/moonweave/witnessd/tests/test_signing_profile.py`

- [ ] **Step 1: Write tests**

```python
import unittest

from witnessd.signing_profile import (
    KEYLESS_FULCIO_REKOR_PROFILE,
    OPERATOR_KEY_PROFILE,
    SigningProfileError,
    select_signing_profile,
)


class TestSigningProfile(unittest.TestCase):
    def test_default_profile_is_operator_key(self):
        profile = select_signing_profile(None)
        self.assertEqual(profile.name, OPERATOR_KEY_PROFILE)
        self.assertEqual(profile.signing_status, "signed-ed25519-operator-key")
        self.assertFalse(profile.signature_boundary["keyless_identity"])
        self.assertFalse(profile.signature_boundary["transparency_logged"])

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(SigningProfileError) as cm:
            select_signing_profile("unknown")
        self.assertEqual(cm.exception.code, "ERR_WITNESSD_SIGNING_PROFILE_UNSUPPORTED")

    def test_keyless_profile_is_always_blocked_in_w6a(self):
        with self.assertRaises(SigningProfileError) as cm:
            select_signing_profile(KEYLESS_FULCIO_REKOR_PROFILE)
        self.assertEqual(cm.exception.code, "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED")
```

- [ ] **Step 2: Run and confirm red**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_signing_profile -v
```

Expected: module missing.

- [ ] **Step 3: Implement `witnessd/signing_profile.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATOR_KEY_PROFILE = "operator-key"
KEYLESS_FULCIO_REKOR_PROFILE = "keyless-fulcio-rekor"


class SigningProfileError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SigningProfile:
    name: str
    signing_status: str
    signature_boundary: dict[str, Any]


def operator_key_signature_boundary() -> dict[str, Any]:
    return {
        "scheme": "DSSE-Ed25519-openssl-cli",
        "operator_key": True,
        "public_verifiable": True,
        "keyless_identity": False,
        "transparency_logged": False,
        "note": (
            "Trust is rooted in the operator-held key and distributed public "
            "key; this is not Fulcio keyless identity or Rekor logging."
        ),
    }


def select_signing_profile(requested: str | None) -> SigningProfile:
    profile = requested or OPERATOR_KEY_PROFILE
    if profile == OPERATOR_KEY_PROFILE:
        return SigningProfile(
            name=OPERATOR_KEY_PROFILE,
            signing_status="signed-ed25519-operator-key",
            signature_boundary=operator_key_signature_boundary(),
        )
    if profile == KEYLESS_FULCIO_REKOR_PROFILE:
        raise SigningProfileError("ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED")
    raise SigningProfileError("ERR_WITNESSD_SIGNING_PROFILE_UNSUPPORTED")
```

- [ ] **Step 4: Refactor substrate boundary helper**

In `witnessd/substrate.py`, replace `_operator_key_signature_boundary()` with a call to `witnessd.signing_profile.operator_key_signature_boundary`. Do not change emitted JSON for operator-key bundles.

- [ ] **Step 5: Run tests**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_signing_profile tests.test_substrate -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add witnessd/signing_profile.py witnessd/substrate.py tests/test_signing_profile.py
git commit -m "feat: add fail-closed signing profile seam"
```

## Task 4: witnessd Bundle Guard

**Files:**
- Modify: `/home/ubuntu/moonweave/witnessd/witnessd/substrate.py`
- Create: `/home/ubuntu/moonweave/witnessd/tests/test_substrate_keyless_guard.py`

- [ ] **Step 1: Write tests**

```python
import tempfile
import unittest
from pathlib import Path

from witnessd.signing_profile import SigningProfileError
from witnessd.substrate import build_bundle


class TestSubstrateKeylessGuard(unittest.TestCase):
    def test_build_bundle_rejects_keyless_profile(self):
        manifest = {
            "kind": "capture-manifest",
            "assurance": "A2-isolated-observed",
            "decision": "accepted",
            "prev_capture_hash": None,
        }
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            with self.assertRaises(SigningProfileError) as cm:
                build_bundle(
                    manifest,
                    {"artifact": str(artifact)},
                    signing_profile="keyless-fulcio-rekor",
                )
            self.assertEqual(cm.exception.code, "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED")

    def test_unsigned_default_still_works(self):
        manifest = {
            "kind": "capture-manifest",
            "assurance": "A2-isolated-observed",
            "decision": "accepted",
            "prev_capture_hash": None,
        }
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            bundle = build_bundle(manifest, {"artifact": str(artifact)})
            self.assertEqual(bundle["signing_status"], "unsigned-content-addressed")
            self.assertNotIn("signature_boundary", bundle)
```

- [ ] **Step 2: Run and confirm red**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_substrate_keyless_guard -v
```

Expected: `build_bundle` has no `signing_profile` keyword.

- [ ] **Step 3: Add guarded keyword-only parameter**

Extend `build_bundle` signature:

```python
def build_bundle(
    manifest: dict[str, Any],
    artifacts: dict[str, str],
    private_key_path: str | None = None,
    public_key_path: str | None = None,
    *,
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
    otel_spans: list[dict[str, Any]] | None = None,
    signing_profile: str | None = None,
) -> dict[str, Any]:
```

At the top of `build_bundle`, before statement construction:

```python
from witnessd.signing_profile import OPERATOR_KEY_PROFILE, select_signing_profile

profile = select_signing_profile(signing_profile)
if profile.name != OPERATOR_KEY_PROFILE:
    raise AssertionError("non-operator signing profile escaped fail-closed selection")
```

No `production_gate_open` parameter is allowed in W6a.

- [ ] **Step 4: Run tests**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_substrate_keyless_guard tests.test_signing_profile tests.test_substrate -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add witnessd/substrate.py tests/test_substrate_keyless_guard.py
git commit -m "feat: block keyless bundle emission in W6a"
```

## Task 5: Cross-Repo W6a Revalidation

**Files:**
- Create: `/home/ubuntu/moonweave/witnessd/scripts/revalidate_w6_keyless.py`

- [ ] **Step 1: Create script**

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPONE = Path("/home/ubuntu/moonweave/depone")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEPONE))

from depone._resources import resource_text
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.keyless import lint_keyless_bundle_fixture
from depone.agent_fabric.sign import verify_signed_bundle
from scripts.revalidate_key_rotation import ARCHIVE, _load, validate_archive
from witnessd.signing_profile import SigningProfileError, select_signing_profile


def _fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    archive = _load(ARCHIVE)
    validate_archive(archive)
    gate = archive["production_gate"]
    if gate["status"] != "open":
        _fail("W6a expects production gate to remain open")
    if len(gate["required_evidence"]) != 5:
        _fail("W6a expects five production gate evidence entries")
    if any(item.get("status") != "recorded" for item in gate["required_evidence"]):
        _fail("W6a expects every production gate evidence entry to be recorded")

    try:
        select_signing_profile("keyless-fulcio-rekor")
    except SigningProfileError as exc:
        if exc.code != "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED":
            raise
    else:
        _fail("keyless profile must fail closed until live Fulcio/Rekor verification exists")

    capture = json.loads(resource_text("fixtures/agent_fabric/keyless/keyless-capture-manifest.json"))
    if validate_capture_manifest(capture) != []:
        _fail("keyless fixture subject must be a valid capture manifest")

    bundle = json.loads(resource_text("fixtures/agent_fabric/keyless/keyless-bundle.json"))
    pinned = resource_text("fixtures/agent_fabric/keyless/keyless-bundle.sha256").strip()
    operator_public_key = ROOT / "fixtures" / "w1" / "keys" / "operator.pub"
    if not operator_public_key.is_file():
        _fail("operator public key fixture missing")
    if verify_signed_bundle(bundle, str(operator_public_key)):
        _fail("operator verifier must reject keyless fixture")
    report = lint_keyless_bundle_fixture(bundle, expected_bundle_sha256=pinned)
    if report.get("decision") != "lint_passed":
        _fail(f"keyless fixture lint failed: {report}")
    if report.get("signature_verified") is not False:
        _fail("W6a keyless lint must not verify signatures")
    if report.get("boundary", {}).get("trusts_external_signature") is not False:
        _fail("W6a keyless lint must not trust external signatures")

    forged = json.loads(resource_text("fixtures/agent_fabric/keyless/negative-forged-self-consistent.json"))
    forged_report = lint_keyless_bundle_fixture(forged, expected_bundle_sha256=pinned)
    if forged_report.get("decision") != "blocked":
        _fail("forged self-consistent keyless fixture must be blocked by pin")

    fake_subject = json.loads(resource_text("fixtures/agent_fabric/keyless/negative-fake-subject.json"))
    fake_subject_report = lint_keyless_bundle_fixture(fake_subject, expected_bundle_sha256=None)
    if fake_subject_report.get("decision") != "blocked":
        _fail("keyless fixture with mismatched subject digest must be blocked")
    if "subject digest mismatch" not in fake_subject_report.get("reasons", []):
        _fail("fake subject fixture must fail for subject digest mismatch")

    result = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_substrate_keyless_guard", "tests.test_signing_profile"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(result.stdout + result.stderr)
    print("W6a keyless readiness revalidate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run script**

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w6_keyless.py
```

Expected: `W6a keyless readiness revalidate: PASS`.

- [ ] **Step 3: Run dependent validation**

```bash
cd /home/ubuntu/moonweave/depone
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_fabric_keyless_lint tests.test_agent_fabric_sign tests.test_agent_fabric_evidence_ingest -v
python3 scripts/check_contract.py --tier changed
python3 scripts/dwm.py doctor

cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_key_rotation_archive tests.test_substrate_keyless_guard tests.test_signing_profile tests.test_substrate -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_key_rotation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w6_keyless.py
```

Expected: all pass; `ingest_signed_evidence_bundle` remains operator-key-only.

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/moonweave/witnessd
git add scripts/revalidate_w6_keyless.py
git commit -m "test: add W6a keyless readiness revalidation"
```

## Task 6: Documentation Update

**Files:**
- Modify: `/home/ubuntu/moonweave/witnessd/SPEC.md`
- Modify: `/home/ubuntu/moonweave/witnessd/docs/plans/README.md`

- [ ] **Step 1: Update SPEC section 3.10**

Replace the single deferred paragraph with this split:

```markdown
W6a implements only non-trusting keyless fixture lint and a witnessd fail-closed signing-profile seam. The production gate is already open through `external-team-pilot` evidence, but W6a does not perform live Fulcio issuance, live Rekor lookup, OIDC exchange, keyless ingest, trusted keyless signature verification, or assurance upgrade. W6b/live keyless remains deferred until an explicit dependency/network verification design is approved.
```

- [ ] **Step 2: Update roadmap row**

Add W6a to `docs/plans/README.md`:

```markdown
| **W6a** | `2026-07-02-w6-keyless-signing.md` | Non-trusting keyless fixture linter + fail-closed witnessd signing profile seam; no keyless ingest or production enablement | `scripts/revalidate_w6_keyless.py`, `scripts/revalidate_key_rotation.py`, W1-W5 revalidators | W5 + key rotation hardening |
```

- [ ] **Step 3: Run doc and gate scans**

```bash
cd /home/ubuntu/moonweave/witnessd
rg -n "signature_verified.*true|trusts_external_signature.*true|production_gate_open|keyless.*ingest|A3|deprecat.*operator" SPEC.md docs/plans docs/ops || true
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_key_rotation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w6_keyless.py
```

Expected: search hits, if any, are negative tests, anti-claims, or explicit W6b-deferred wording; no positive trusted-keyless, A3, production-gate-open flag, keyless-ingest, or operator-key deprecation claim is introduced. Both revalidators pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/moonweave/witnessd
git add SPEC.md docs/plans/README.md docs/plans/2026-07-02-w6-keyless-signing.md
git commit -m "docs: plan W6a keyless readiness"
```

## Final Validation Matrix

Run these before claiming W6a implementation complete:

```bash
cd /home/ubuntu/moonweave/depone
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
python3 scripts/check_contract.py --tier changed
python3 scripts/dwm.py doctor

cd /home/ubuntu/moonweave/witnessd
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m witnessd self-test --all
for n in 1 2 3 4 5; do PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 "scripts/revalidate_w${n}.py"; done
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_key_rotation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w6_keyless.py
git diff --check
```

Expected:
- Depone tests pass and contract/doctor pass.
- witnessd tests pass, self-test passes, W1-W5 revalidators pass, key rotation passes, W6a revalidator passes.
- `fixtures/key-rotation/operator-key-archive.json` still has `production_gate.status == "open"` and all five required evidence entries recorded.
- No W6a path produces trusted keyless signature semantics.

## Review Checklist For Agents

- Verify `ingest_signed_evidence_bundle` is untouched and remains operator-key-only.
- Verify keyless lint never returns `signature_verified=true`.
- Verify keyless lint never sets `trusts_external_signature=true`.
- Verify keyless lint never sets `keyless_identity=true` or `transparency_logged=true`.
- Verify statement subject digest is bound to the embedded Depone-valid capture manifest.
- Verify keyless profile selection has no caller-controlled gate boolean.
- Verify operator-key signing remains default and tested.
- Verify the production gate remains open and unchanged.
- Verify W6b/live keyless remains a separate dependency/network design.
