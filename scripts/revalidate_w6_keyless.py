#!/usr/bin/env python3
"""Revalidate W6a keyless readiness against the open production gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEPONE = Path(os.environ.get("WITNESSD_DEPONE_ROOT", ROOT.parent / "depone"))
for candidate in (ROOT, DEPONE):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from depone._resources import resource_text
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.keyless import lint_keyless_bundle_fixture
from depone.agent_fabric.sign import verify_signed_bundle
from scripts.revalidate_key_rotation import ARCHIVE, _load, validate_archive
from witnessd.signing_profile import (
    KEYLESS_FULCIO_REKOR_PROFILE,
    SigningProfileError,
    select_signing_profile,
)


SAFETY_MESSAGE = (
    "gate open, but witnessd cannot emit keyless evidence and Depone does not "
    "trust keyless metadata because live Fulcio/Rekor verification is W6b work"
)


def _fail(message: str) -> None:
    raise AssertionError(message)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads(resource_text(f"fixtures/agent_fabric/keyless/{name}"))


def _assert_open_gate() -> None:
    archive = _load(ARCHIVE)
    validate_archive(archive)
    gate = archive["production_gate"]
    if gate["status"] != "open":
        _fail("W6a expects the external-team-pilot production gate to be open")
    required = gate.get("required_evidence")
    if not isinstance(required, list) or len(required) != 5:
        _fail("production gate must carry the five required evidence records")
    if any(item.get("status") != "recorded" for item in required):
        _fail("open production gate must have all five evidence records recorded")


def _assert_keyless_profile_fails_closed() -> None:
    try:
        select_signing_profile(KEYLESS_FULCIO_REKOR_PROFILE)
    except SigningProfileError as exc:
        if exc.code != "ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED":
            raise
    else:
        _fail("keyless profile must remain fail-closed until W6b live verification")


def _assert_linter_is_non_trusting(report: dict[str, Any]) -> None:
    if report.get("signature_verified") is not False:
        _fail("W6a keyless lint must not verify signatures")
    if report.get("trusts_external_signature") is not False:
        _fail("W6a keyless lint must not trust external signatures")
    if report.get("keyless_identity") is not False:
        _fail("W6a keyless lint must not trust keyless identity")
    if report.get("transparency_logged") is not False:
        _fail("W6a keyless lint must not claim transparency logging")


def _assert_keyless_fixture_lint() -> None:
    capture = _fixture("keyless-capture-manifest.json")
    if validate_capture_manifest(capture) != []:
        _fail("keyless fixture subject must be a valid capture manifest")

    bundle = _fixture("keyless-bundle.json")
    pinned = resource_text("fixtures/agent_fabric/keyless/keyless-bundle.sha256").strip()
    operator_public_key = ROOT / "fixtures" / "w1" / "keys" / "operator.pub"
    if not operator_public_key.is_file():
        _fail("operator public key fixture missing")
    if verify_signed_bundle(bundle, str(operator_public_key)):
        _fail("operator verifier must reject keyless fixture")

    report = lint_keyless_bundle_fixture(bundle, expected_bundle_sha256=pinned)
    if report.get("decision") != "lint_passed":
        _fail(f"keyless fixture lint failed: {report}")
    _assert_linter_is_non_trusting(report)
    boundary = report.get("boundary", {})
    if not isinstance(boundary, dict) or boundary.get("trusts_external_signature") is not False:
        _fail("W6a keyless boundary must not trust external signatures")

    forged_report = lint_keyless_bundle_fixture(
        _fixture("negative-forged-self-consistent.json"),
        expected_bundle_sha256=pinned,
    )
    if forged_report.get("decision") != "blocked":
        _fail("forged self-consistent keyless fixture must be blocked by pin")
    if "fixture hash mismatch" not in forged_report.get("reasons", []):
        _fail("forged keyless fixture must fail for fixture hash mismatch")
    _assert_linter_is_non_trusting(forged_report)

    fake_subject_report = lint_keyless_bundle_fixture(
        _fixture("negative-fake-subject.json"),
        expected_bundle_sha256=None,
    )
    if fake_subject_report.get("decision") != "blocked":
        _fail("keyless fixture with mismatched subject digest must be blocked")
    if "subject digest mismatch" not in fake_subject_report.get("reasons", []):
        _fail("fake subject fixture must fail for subject digest mismatch")
    _assert_linter_is_non_trusting(fake_subject_report)

    a3_report = lint_keyless_bundle_fixture(
        _fixture("negative-assurance-upgrade.json"),
        expected_bundle_sha256=None,
    )
    if a3_report.get("decision") != "blocked":
        _fail("A3 keyless fixture must be blocked")
    if "assurance exceeds A2" not in a3_report.get("reasons", []):
        _fail("A3 keyless fixture must fail for assurance ceiling")
    _assert_linter_is_non_trusting(a3_report)


def _assert_witnessd_keyless_tests() -> None:
    env = os.environ.copy()
    depone_path = str(DEPONE)
    env["PYTHONPATH"] = (
        depone_path
        if not env.get("PYTHONPATH")
        else depone_path + os.pathsep + env["PYTHONPATH"]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_substrate_keyless_guard",
            "tests.test_signing_profile",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(result.stdout + result.stderr)


def main() -> int:
    _assert_open_gate()
    _assert_keyless_profile_fails_closed()
    _assert_keyless_fixture_lint()
    _assert_witnessd_keyless_tests()
    print(SAFETY_MESSAGE)
    print("W6a keyless readiness revalidate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
